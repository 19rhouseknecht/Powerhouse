/* Make every current and future date/time input obvious and touch friendly. */
document.addEventListener("DOMContentLoaded", () => {
  const selector = 'input[type="date"], input[type="datetime-local"], input[type="time"]';
  document.querySelectorAll(selector).forEach(input => {
    if (input.closest(".date-picker-control")) return;
    if ((input.type === "datetime-local" || input.type === "time") && !input.step) input.step = "900";

    const control = document.createElement("span");
    control.className = `date-picker-control date-picker-${input.type}`;
    input.parentNode.insertBefore(control, input);
    control.append(input);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "date-picker-button";
    button.setAttribute("aria-label", input.type === "date" ? "Open calendar" : input.type === "time" ? "Choose time" : "Open calendar and time picker");
    button.innerHTML = `<span aria-hidden="true">${input.type === "time" ? "◷" : "▦"}</span><b>${input.type === "date" ? "Choose date" : input.type === "time" ? "Choose time" : "Choose date & time"}</b>`;
    control.append(button);

    const openPicker = () => {
      input.focus({preventScroll: true});
      if (typeof input.showPicker === "function") {
        try { input.showPicker(); return; } catch (_error) { /* Fall back below. */ }
      }
      input.click();
    };
    button.addEventListener("click", openPicker);
  });
});
