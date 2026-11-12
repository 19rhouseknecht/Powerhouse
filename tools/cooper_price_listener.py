"""Always-on visible Cooper price listener for Power House Flow.

First install:
    python3 -m pip install playwright

Run once and leave the terminal open:
    python3 cooper_price_listener.py

On first run, enter the one-time token shown under Settings -> Local Cooper
Updater plus the Cooper username and password. They are stored only in the
local config file beside this script. The listener then waits for Update Prices
commands from the website. Chrome opens only while a scan is running. Results
always require review and approval in Power House Flow; this program never
applies prices directly. Product pictures are sent as each item is scanned.

Options:
    --tabs 3           how many Cooper tabs scan at once (1-6, default 3)
    --once             process one command, or exit if none is queued
    --reset-config     forget the saved token and Cooper login
    --poll-seconds 5   how often to ask the website for work

Version 2 is much faster than version 1: no artificial slow-down, the cookie
banner is handled once, several tabs scan at once, pictures/fonts/trackers are
not downloaded for search pages, and an existing Cooper login is reused.
"""

from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen
import argparse
import asyncio
import base64
import json
import os
import re
import time
from getpass import getpass

LISTENER_VERSION = "2.0"
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIRECTORY / "cooper_updater_config.json"
PROFILE_DIRECTORY = SCRIPT_DIRECTORY / "cooper_chrome_profile"
COOPER_HOME = os.environ.get("COOPER_HOME", "https://www.cooper-electric.com/")
BROWSER_CHANNEL = os.environ.get("COOPER_BROWSER_CHANNEL", "chrome")      # "" = Playwright's own Chromium
MAX_IMAGE_BYTES = 3 * 1024 * 1024
PRICE_WAIT_SECONDS = 30
BLOCKED_TYPES = {"image", "media", "font"}
BLOCKED_HOSTS = ("google-analytics.", "googletagmanager.", "doubleclick.", "facebook.", "hotjar.", "clarity.ms", "bing.com",
                 "linkedin.", "tiktok.", "pinterest.", "hubspot.", "adservice.", "youtube.")


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} — {message}", flush=True)


# --------------------------------------------------------------------------- config and the website
def load_or_create_config(reset: bool = False) -> dict:
    if reset and CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
        log("Saved updater connection removed.")
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    config["base_url"] = os.environ.get("POWERHOUSE_URL", config.get("base_url") or "https://www.powerhouseflow.com").rstrip("/")
    if not config.get("token"):
        print("\nPower House Flow connection setup")
        print("Generate a token under Settings -> Local Cooper Updater first.\n")
        config["token"] = input("Local Cooper Updater token: ").strip()
    if not config.get("cooper_username"):
        config["cooper_username"] = input("Cooper username: ").strip()
    if not config.get("cooper_password"):
        config["cooper_password"] = getpass("Cooper password: ")
    if not config["token"] or not config["cooper_username"] or not config["cooper_password"]:
        raise ValueError("The Power House token and Cooper username/password are required.")
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    log(f"Connection saved locally in {CONFIG_PATH.name}.")
    return config


def api_request(config: dict, path: str, method: str = "GET", payload=None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{config['base_url']}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {config['token']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"PowerHouseFlow-LocalCooperUpdater/{LISTENER_VERSION}",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("error", body)
        except json.JSONDecodeError:
            detail = body
        raise RuntimeError(f"Power House Flow returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Power House Flow: {error.reason}") from error


# --------------------------------------------------------------------------- reading Cooper's pages
def parse_source_quantity_and_unit(uom_text: str) -> tuple:
    packaged = re.search(
        r"\(?\s*([0-9,]+(?:\.\d+)?)\s*(ft|feet|foot|ea|each|pc|piece|pieces|box|roll|lb|pound)\s*\)?",
        uom_text,
        re.IGNORECASE,
    )
    aliases = {
        "feet": "ft", "foot": "ft", "ft": "ft",
        "each": "ea", "ea": "ea", "pc": "ea", "piece": "ea", "pieces": "ea",
        "box": "box", "roll": "roll", "lb": "lb", "pound": "lb",
    }
    if packaged:
        return float(packaged.group(1).replace(",", "")), aliases[packaged.group(2).casefold()]

    slash_unit = re.search(r"/(ea|each|ft|box|roll|lb)\b", uom_text, re.IGNORECASE)
    if slash_unit:
        return 1.0, aliases[slash_unit.group(1).casefold()]
    raise ValueError(f"Could not determine Cooper package quantity/unit from {uom_text!r}.")


def picture_url(card_html_attrs: dict, page_url: str) -> str:
    """The product picture's address from an <img>'s attributes (lazy-loaded pictures keep it in data-src or srcset)."""
    for key in ("data-src", "data-original", "data-lazy-src", "src"):
        value = (card_html_attrs.get(key) or "").strip()
        if value and not value.startswith("data:"):
            return urljoin(page_url, value)
    srcset = (card_html_attrs.get("srcset") or card_html_attrs.get("data-srcset") or "").strip()
    if srcset:
        return urljoin(page_url, srcset.split(",")[-1].strip().split(" ")[0])
    return ""


async def block_extras(route):
    """Search pages do not need pictures, fonts, video or trackers: skipping them is most of the page weight."""
    request = route.request
    if request.resource_type in BLOCKED_TYPES or any(host in request.url for host in BLOCKED_HOSTS):
        await route.abort()
    else:
        await route.continue_()


async def verification_page_is_present(page) -> bool:
    return bool(await page.locator('script[src*="_Incapsula_Resource"]').count() or await page.locator("iframe#main-iframe").count())


async def wait_for_verification(page) -> None:
    if not await verification_page_is_present(page):
        return
    log("Cooper displayed browser verification. Waiting for it to finish.")
    for _ in range(60):
        await page.wait_for_timeout(1_000)
        if not await verification_page_is_present(page):
            return
    log("Verification is still showing; continuing anyway.")


async def accept_cookies(page, wait_seconds: float = 0) -> bool:
    """Click Cooper's cookie banner if it is showing. Waits only when asked (the first page of a run)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        for selector in ('button:text-is("Accept All")', 'button:text-is("Accept all")', 'button:text-is("Allow all")', 'button:has-text("Accept All")'):
            button = page.locator(selector).first
            if await button.count() and await button.is_visible():
                await button.click(force=True)
                log("Cookie banner accepted.")
                return True
        if time.monotonic() >= deadline:
            return False
        await page.wait_for_timeout(250)


async def login_trigger(page):
    for selector in ('button.dropdown-toggle:has-text("Log In")', 'button.dropdown-toggle:has-text("Login")',
                     'a.dropdown-toggle:has-text("Log In")', 'a.dropdown-toggle:has-text("Login")'):
        found = page.locator(selector).first
        if await found.count() and await found.is_visible():
            return found
    for role in ("button", "link"):
        found = page.get_by_role(role, name=re.compile(r"^(log in|login|sign in)$", re.I)).first
        if await found.count() and await found.is_visible():
            return found
    return None


async def login_to_cooper(page, config: dict) -> None:
    """Sign in through Cooper's login dropdown, unless the saved Chrome profile is still signed in."""
    trigger = await login_trigger(page)
    if trigger is None:
        log("Already signed in to Cooper (saved browser session). Skipping login.")
        return
    log("Signing in to Cooper.")
    await trigger.click(force=True)
    username_input = page.locator("#UserName, input[name='UserName']").first
    password_input = page.locator("#Password, input[name='Password']").first
    await username_input.wait_for(state="visible", timeout=20_000)
    await username_input.fill(config["cooper_username"])
    await password_input.fill(config["cooper_password"])
    # The header also has a search submit button: only use the submit inside the login form.
    form = username_input.locator("xpath=ancestor::form[1]")
    if not await form.count():
        raise RuntimeError("Cooper's username field is visible, but its login form was not found.")
    submit = form.get_by_role("button", name=re.compile(r"^(login|log in|sign in|submit)$", re.I)).first
    if not await submit.count():
        submit = form.locator('button[type="submit"], input[type="submit"]').first
    if not await submit.count():
        raise RuntimeError("Cooper's login form appeared, but its submit button was not found.")
    await submit.click(force=True)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20_000)
        await password_input.wait_for(state="hidden", timeout=15_000)
    except Exception:  # noqa: BLE001
        await page.wait_for_timeout(1_000)
    await accept_cookies(page)
    if await password_input.count() and await password_input.is_visible():
        raise RuntimeError("Cooper login did not complete. Check the saved username and password (run with --reset-config).")
    log("Signed in. Account pricing is available.")


async def scan_item(page, item: dict, label: str) -> dict:
    part_number = str(item["part_number"]).strip()
    await page.goto(f"{COOPER_HOME.rstrip('/')}/product/search?q={quote_plus(part_number)}", wait_until="domcontentloaded", timeout=60_000)
    await wait_for_verification(page)
    await accept_cookies(page)

    cards = page.locator("#productSearchResults div.product.productCard")
    await cards.first.wait_for(state="attached", timeout=45_000)
    exact_card, returned_ids = None, []
    for index in range(await cards.count()):
        candidate = cards.nth(index)
        candidate_id = ((await candidate.get_attribute("data-product-id")) or "").strip()
        if candidate_id:
            returned_ids.append(candidate_id)
        if candidate_id.casefold() == part_number.casefold():
            exact_card = candidate
            break
    if exact_card is None:
        raise RuntimeError(f"No exact product ID match. Returned IDs included {returned_ids[:10]}.")

    price_node = exact_card.locator("span.pricerPerQty, span.pricePerQty, [data-analytics-price]").first
    await price_node.wait_for(state="attached", timeout=PRICE_WAIT_SECONDS * 1000)
    raw_price = ""
    for _ in range(PRICE_WAIT_SECONDS * 5):
        raw_price = ((await price_node.get_attribute("data-analytics-price")) or "").strip()
        if raw_price:
            break
        await page.wait_for_timeout(200)
    if not raw_price:
        raise RuntimeError(f"The product appeared, but its price stayed blank for {PRICE_WAIT_SECONDS} seconds.")

    source_price = float(raw_price.replace(",", ""))
    source_uom = " ".join(text.strip() for text in await exact_card.locator("small.priceUOM").all_text_contents() if text.strip())
    source_quantity, source_unit = parse_source_quantity_and_unit(source_uom)
    name_node = exact_card.locator("[data-analytics-description]").first
    matched_name = (await name_node.get_attribute("data-analytics-description")) if await name_node.count() else item["name"]
    image = ""
    img = exact_card.locator("img").first
    if await img.count():
        attrs = await img.evaluate("e => Object.fromEntries(Array.from(e.attributes).map(a => [a.name, a.value]))")
        image = picture_url(attrs, page.url)
    log(f"{label} {part_number}: ${source_price:,.2f} {source_uom} → ${source_price / source_quantity:,.4f}/{source_unit}")
    return {
        "item_id": item["id"],
        "part_number": part_number,
        "name": matched_name,
        "status": "matched",
        "source_price": source_price,
        "source_quantity": source_quantity,
        "source_unit": source_unit,
        "source_uom": source_uom,
        "image_url": image,
    }


async def fetch_picture(context, url: str, referer: str) -> bytes:
    """Download the product picture with the browser's own session (pictures are blocked on the search pages)."""
    if not url:
        return b""
    try:
        response = await context.request.get(url, headers={"Referer": referer}, timeout=20_000)
        body = await response.body()
        if response.ok and 0 < len(body) <= MAX_IMAGE_BYTES:
            return body
    except Exception as error:  # noqa: BLE001 — a missing picture never stops a price check
        log(f"Picture not downloaded ({type(error).__name__}).")
    return b""


async def scan_all_items(items: list, config: dict, progress=None, send_picture=None, tabs: int = 3, headless: bool = False) -> list:
    """Scan every item with up to `tabs` Cooper tabs at once. Results come back in the items' order."""
    from playwright.async_api import async_playwright

    results = [None] * len(items)
    done = 0
    queue = asyncio.Queue()
    for index, item in enumerate(items):
        queue.put_nowait((index, item))

    async with async_playwright() as playwright:
        options = {"user_data_dir": str(PROFILE_DIRECTORY), "headless": headless, "no_viewport": not headless}
        if BROWSER_CHANNEL:
            options["channel"] = BROWSER_CHANNEL
        executable = os.environ.get("COOPER_BROWSER_PATH")
        if executable:
            options["executable_path"] = executable
        context = await playwright.chromium.launch_persistent_context(**options)
        try:
            first = context.pages[0] if context.pages else await context.new_page()
            log(f"Opening {COOPER_HOME}")
            await first.goto(COOPER_HOME, wait_until="domcontentloaded", timeout=60_000)
            await wait_for_verification(first)
            await accept_cookies(first, wait_seconds=8)
            await login_to_cooper(first, config)
            await context.route("**/*", block_extras)       # after login, so the sign-in page loads normally

            total = len(items)
            tab_count = max(1, min(tabs, 6, total or 1))
            pages = [first] + [await context.new_page() for _ in range(tab_count - 1)]
            log(f"Scanning {total} item(s) in {tab_count} tab(s).")
            started = time.monotonic()

            async def worker(page, tab_number):
                nonlocal done
                while True:
                    try:
                        index, item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    label = f"[{index + 1}/{total} tab {tab_number}]"
                    try:
                        result = await scan_item(page, item, label)
                        if send_picture and result.get("image_url"):
                            picture = await fetch_picture(context, result["image_url"], page.url)
                            if picture:
                                await asyncio.to_thread(send_picture, item, picture)
                    except Exception as error:  # noqa: BLE001 — one item's problem never stops the others
                        log(f"{label} {item['part_number']}: could not scan ({type(error).__name__}: {error})")
                        result = {"item_id": item["id"], "part_number": item["part_number"], "status": "error",
                                  "error": f"{type(error).__name__}: {error}"[:500]}
                    results[index] = result
                    done += 1
                    if progress:
                        try:
                            await asyncio.to_thread(progress, done, total, f"Finished {done}/{total}: Cooper #{item['part_number']}")
                        except Exception as error:  # noqa: BLE001
                            log(f"Progress update not sent ({error}).")

            await asyncio.gather(*(worker(page, number) for number, page in enumerate(pages, 1)))
            seconds = time.monotonic() - started
            log(f"Scanned {total} item(s) in {seconds:,.0f}s ({seconds / max(total, 1):.1f}s per item).")
        finally:
            log("Scanning finished. Closing Chrome.")
            await context.close()
    return results


# --------------------------------------------------------------------------- the loop
def main() -> None:
    parser = argparse.ArgumentParser(description="Listen for Power House Flow Cooper price commands.")
    parser.add_argument("--reset-config", action="store_true", help="Forget the saved token and Cooper login.")
    parser.add_argument("--once", action="store_true", help="Process one command, or exit if none is queued.")
    parser.add_argument("--poll-seconds", type=int, default=5, help="Seconds between command checks (default: 5).")
    parser.add_argument("--tabs", type=int, default=3, help="Cooper tabs that scan at once, 1-6 (default: 3).")
    parser.add_argument("--headless", action="store_true", help="Scan without showing Chrome (Cooper may ask for verification more often).")
    args = parser.parse_args()
    config = load_or_create_config(reset=args.reset_config)

    poll_seconds = max(args.poll_seconds, 2)
    log(f"Listener v{LISTENER_VERSION} connected to {config['base_url']}. Checking for Update Prices commands every {poll_seconds} seconds.")
    while True:
        command_id = None
        try:
            response = api_request(config, "/api/local-cooper/commands/next")
            command = response.get("command")
            if not command:
                if args.once:
                    log("No price update is queued.")
                    return
                time.sleep(poll_seconds)
                continue
            command_id = command["id"]
            items = command.get("items", [])
            log(f"Received price check #{command_id} with {len(items)} Cooper item(s).")
            if not items:
                raise RuntimeError("The queued command did not contain any Cooper items.")

            def heartbeat(current, total, message):
                api_request(config, f"/api/local-cooper/commands/{command_id}/heartbeat", method="POST",
                            payload={"current": current, "total": total, "message": message})

            def send_picture(item, data):
                try:
                    api_request(config, f"/api/local-cooper/commands/{command_id}/image", method="POST",
                                payload={"item_id": item["id"], "part_number": item["part_number"], "data": base64.b64encode(data).decode("ascii")})
                except RuntimeError as error:
                    log(f"Picture for {item['part_number']} not saved: {error}")

            results = asyncio.run(scan_all_items(items, config, heartbeat, send_picture, tabs=args.tabs, headless=args.headless))
            log("Uploading results for website review.")
            uploaded = api_request(config, f"/api/local-cooper/commands/{command_id}/results", method="POST",
                                   payload={"results": [{k: v for k, v in r.items() if k != "image_url"} for r in results]})
            log(f"Check #{command_id} ready: {uploaded.get('matched', 0)} matched, {uploaded.get('errors', 0)} errors.")
            log(f"Review it in Power House Flow: {uploaded['preview_url']}")
            if args.once:
                return
        except KeyboardInterrupt:
            raise
        except Exception as error:  # noqa: BLE001
            message = f"{type(error).__name__}: {error}"
            log(f"Price check failed: {message}")
            if command_id is not None:
                try:
                    api_request(config, f"/api/local-cooper/commands/{command_id}/failed", method="POST", payload={"error": message})
                except Exception as report_error:  # noqa: BLE001
                    log(f"Could not report failure to Power House Flow: {report_error}")
            if args.once:
                raise
            time.sleep(poll_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user.")
    except Exception as error:  # noqa: BLE001
        log(f"Updater stopped: {type(error).__name__}: {error}")
        if "401" in str(error):
            print("Run again with --reset-config and enter a newly generated token.")
