import os
import secrets
import calendar
import json
import base64
import hashlib
import re
import stat
import threading
import math
import shutil
import zipfile
from difflib import SequenceMatcher
from functools import wraps
from urllib.parse import quote_plus
from datetime import datetime, date, timedelta

from markupsafe import Markup, escape
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user, login_url,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import text
import httpx
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet, InvalidToken

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "instance", "app.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."


# ---------------- Models ----------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"), nullable=True, index=True)
    hourly_rate = db.Column(db.Float, nullable=False, default=0)
    role = db.relationship("Role", backref="users")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def can(self, permission):
        return bool(self.role and (self.role.is_admin or getattr(self.role, permission, False)))


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    view_quoting = db.Column(db.Boolean, nullable=False, default=True)
    view_pricing = db.Column(db.Boolean, nullable=False, default=False)
    view_services = db.Column(db.Boolean, nullable=False, default=True)
    view_clients = db.Column(db.Boolean, nullable=False, default=True)
    view_jobs = db.Column(db.Boolean, nullable=False, default=True)
    view_schedule = db.Column(db.Boolean, nullable=False, default=True)
    view_all_schedules = db.Column(db.Boolean, nullable=False, default=False)
    view_time = db.Column(db.Boolean, nullable=False, default=True)
    view_all_timesheets = db.Column(db.Boolean, nullable=False, default=False)
    view_tasks = db.Column(db.Boolean, nullable=False, default=True)
    view_training = db.Column(db.Boolean, nullable=False, default=True)
    view_finance = db.Column(db.Boolean, nullable=False, default=False)
    view_expenses = db.Column(db.Boolean, nullable=False, default=True)
    view_sales = db.Column(db.Boolean, nullable=False, default=True)
    manage_users = db.Column(db.Boolean, nullable=False, default=False)
    manage_agents = db.Column(db.Boolean, nullable=False, default=False)


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(100), nullable=False, default="General")
    description = db.Column(db.Text, nullable=False, default="")
    example_quantity = db.Column(db.String(80), nullable=False, default="1 job")
    drawing_filename = db.Column(db.String(255))
    estimated_labor_hours = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    items = db.relationship("ServiceItem", backref="service", cascade="all, delete-orphan", order_by="ServiceItem.position")
    tools = db.relationship("ServiceTool", backref="service", cascade="all, delete-orphan", order_by="ServiceTool.position")

    @property
    def example_total(self):
        return self.material_total + self.labor_total

    @property
    def material_total(self):
        return self.item_material_total + self.rental_total

    @property
    def item_material_total(self):
        return sum((item.quantity or 0) * item.current_unit_price for item in self.items)

    @property
    def rental_total(self):
        return sum(tool.current_cost for tool in self.tools)

    @property
    def labor_total(self):
        return (self.estimated_labor_hours or 0) * get_labor_rate()


class PriceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    lookup_key = db.Column(db.String(180), unique=True, nullable=False, index=True)
    category = db.Column(db.String(100), nullable=False, default="General")
    unit = db.Column(db.String(40), nullable=False, default="ea")
    price = db.Column(db.Float, nullable=False, default=0)
    labor_hours_per_unit = db.Column(db.Float, nullable=False, default=0)
    cooper_part_number = db.Column(db.String(100), nullable=False, default="")
    notes = db.Column(db.String(255), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    image_path = db.Column(db.String(255), nullable=False, default="")       # under static/uploads/price-items/
    image_source = db.Column(db.String(20), nullable=False, default="")      # '' · 'cooper' (captured by the listener) · 'upload'
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    service_links = db.relationship("ServiceItemPricingLink", backref="price_item", cascade="all, delete-orphan")
    quote_lines = db.relationship("QuoteOptionService", back_populates="price_item")
    supplier_parts = db.relationship("SupplierPartNumber", backref="price_item", cascade="all, delete-orphan", order_by="SupplierPartNumber.supplier")

    @property
    def linked_services(self):
        """Return each linked Service once, sorted for Pricing Sheet display."""
        services = {
            link.service_item.service.id: link.service_item.service
            for link in self.service_links
            if link.service_item and link.service_item.service
        }
        return sorted(services.values(), key=lambda service: service.name.casefold())


class SupplierPartNumber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    price_item_id = db.Column(db.Integer, db.ForeignKey("price_item.id"), nullable=False, index=True)
    supplier = db.Column(db.String(100), nullable=False, default="Cooper Electric")
    part_number = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("price_item_id", "supplier", "part_number", name="uq_price_item_supplier_part"),)


class SupplierCredential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier = db.Column(db.String(60), unique=True, nullable=False, index=True)
    username = db.Column(db.String(180), nullable=False)
    encrypted_password = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_tested_at = db.Column(db.DateTime, nullable=True)
    last_test_ok = db.Column(db.Boolean, nullable=True)
    last_error = db.Column(db.String(255), nullable=False, default="")


class PriceSyncBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier = db.Column(db.String(60), nullable=False, default="cooper")
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    applied_at = db.Column(db.DateTime, nullable=True)
    created_by = db.relationship("User")
    rows = db.relationship("PriceSyncRow", backref="batch", cascade="all, delete-orphan", order_by="PriceSyncRow.id")
    run = db.relationship("PriceSyncRun", backref="batch", uselist=False, cascade="all, delete-orphan")


class PriceSyncRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("price_sync_batch.id"), unique=True, nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="queued")
    current = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    activity_log = db.Column(db.Text, nullable=False, default="[]")
    error_message = db.Column(db.String(255), nullable=False, default="")
    only_zero_prices = db.Column(db.Boolean, nullable=False, default=False)
    item_ids_json = db.Column(db.Text, nullable=False, default="")          # "Get selected prices": just these items
    mode = db.Column(db.String(20), nullable=False, default="")             # '' prices · 'catalog' add Cooper's items
    limit_items = db.Column(db.Integer, nullable=False, default=0)          # catalog: new items this run may add (0 = no limit)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def item_ids(self):
        try:
            value = json.loads(self.item_ids_json or "[]")
            return [int(v) for v in value if isinstance(v, int) or str(v).isdigit()]
        except ValueError:
            return []


class PriceSyncRow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("price_sync_batch.id"), nullable=False, index=True)
    price_item_id = db.Column(db.Integer, db.ForeignKey("price_item.id"), nullable=False, index=True)
    part_number = db.Column(db.String(100), nullable=False)
    old_price = db.Column(db.Float, nullable=False)
    new_price = db.Column(db.Float, nullable=True)
    matched_name = db.Column(db.String(255), nullable=False, default="")
    status = db.Column(db.String(30), nullable=False, default="pending")
    message = db.Column(db.String(255), nullable=False, default="")
    price_item = db.relationship("PriceItem")


class ServiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit = db.Column(db.String(40), nullable=False, default="ea")
    unit_price = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.String(255), nullable=False, default="")
    position = db.Column(db.Integer, nullable=False, default=0)
    pricing_link = db.relationship("ServiceItemPricingLink", backref="service_item", uselist=False, cascade="all, delete-orphan")

    @property
    def catalog_item(self):
        return self.pricing_link.price_item if self.pricing_link else None

    @property
    def current_name(self):
        return self.catalog_item.name if self.catalog_item else self.name

    @property
    def current_unit(self):
        return self.catalog_item.unit if self.catalog_item else self.unit

    @property
    def current_unit_price(self):
        return self.catalog_item.price if self.catalog_item else (self.unit_price or 0)


class ServiceItemPricingLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_item_id = db.Column(db.Integer, db.ForeignKey("service_item.id"), unique=True, nullable=False, index=True)
    price_item_id = db.Column(db.Integer, db.ForeignKey("price_item.id"), nullable=False, index=True)


class ToolCatalog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    lookup_key = db.Column(db.String(180), unique=True, nullable=False, index=True)
    tool_type = db.Column(db.String(80), nullable=False, default="Other")
    rental_cost = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.String(255), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    service_links = db.relationship("ServiceTool", back_populates="catalog_tool")


class ServiceTool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False, index=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool_catalog.id", ondelete="SET NULL"), nullable=True, index=True)
    name = db.Column(db.String(180), nullable=False)
    tool_type = db.Column(db.String(80), nullable=False, default="Other")
    rental_cost = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.String(255), nullable=False, default="")
    is_critical = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    catalog_tool = db.relationship("ToolCatalog", back_populates="service_links")

    @property
    def current_name(self):
        return self.catalog_tool.name if self.catalog_tool else self.name

    @property
    def current_type(self):
        return self.catalog_tool.tool_type if self.catalog_tool else (self.tool_type or "Other")

    @property
    def current_cost(self):
        cost = self.catalog_tool.rental_cost if self.catalog_tool else self.rental_cost
        return max(cost or 0, 0) if self.current_type.casefold() == "rental" else 0


class CompanySetting(db.Model):
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")


class FlowChart(db.Model):
    """Power House Flow: the business drawn as a flow chart. Stands apart from everything else."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="Power House Flow")
    data_json = db.Column(db.Text, nullable=False, default="")
    version = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    updated_by = db.relationship("User")


class CalebMessage(db.Model):
    """One line of a person's conversation with Ask Caleb. "New conversation" sets cleared; nothing is deleted."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    looked_up = db.Column(db.Text, nullable=False, default="")       # what Caleb looked at for this answer, one per line
    cleared = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class GeneratorUpgrade(db.Model):
    """A generator upgrade saved to a job (Generators -> Upgrading). inputs_json reopens it on the page."""
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    inputs_json = db.Column(db.Text, nullable=False, default="{}")
    summary = db.Column(db.Text, nullable=False, default="")
    total = db.Column(db.Float, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    job = db.relationship("Job", backref=db.backref("generator_upgrades", order_by="GeneratorUpgrade.created_at.desc()"))
    created_by = db.relationship("User")

    @property
    def inputs(self):
        try:
            value = json.loads(self.inputs_json or "{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}


class GeneratorSizing(db.Model):
    """A generator sizing saved to a job (Generators -> Sizing)."""
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    inputs_json = db.Column(db.Text, nullable=False, default="{}")
    required_kw = db.Column(db.Float, nullable=False, default=0)
    model = db.Column(db.String(40), nullable=False, default="")
    summary = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    job = db.relationship("Job", backref=db.backref("generator_sizings", order_by="GeneratorSizing.created_at.desc()"))
    created_by = db.relationship("User")

    @property
    def inputs(self):
        try:
            return json.loads(self.inputs_json or "{}")
        except ValueError:
            return {}


class Release(db.Model):
    """One deploy of a build onto the live site (Settings -> Releases)."""
    id = db.Column(db.Integer, primary_key=True)
    sha = db.Column(db.String(40), nullable=False, default="", index=True)
    version = db.Column(db.String(40), nullable=False, default="")
    title = db.Column(db.String(255), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    author = db.Column(db.String(120), nullable=False, default="")
    committed_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="manual")      # auto, manual, rollback, snapshot
    status = db.Column(db.String(20), nullable=False, default="running")     # running, live, replaced, failed
    by_label = db.Column(db.String(120), nullable=False, default="")
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    files_changed = db.Column(db.Integer, nullable=False, default=0)
    files_removed = db.Column(db.Integer, nullable=False, default=0)
    db_backup = db.Column(db.String(255), nullable=False, default="")
    zip_path = db.Column(db.String(255), nullable=False, default="")
    message = db.Column(db.Text, nullable=False, default="")

    @property
    def short(self):
        return self.sha[:7] if self.sha else "snapshot"

    @property
    def label(self):
        return self.version or self.short


class AiAgent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(180), nullable=False, default="Quoting AI Agent")
    identity = db.Column(db.Text, nullable=False, default="")
    prompt = db.Column(db.Text, nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    knowledge_entries = db.relationship("AgentKnowledge", backref="agent", cascade="all, delete-orphan", order_by="AgentKnowledge.title")
    reference_files = db.relationship("AgentReferenceFile", backref="agent", cascade="all, delete-orphan", order_by="AgentReferenceFile.original_name")


class AgentKnowledge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("ai_agent.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    category = db.Column(db.String(100), nullable=False, default="General")
    content = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class AgentReferenceFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("ai_agent.id"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    extracted_text = db.Column(db.Text, nullable=False, default="")
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class LaborProposalBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference_file_id = db.Column(db.Integer, db.ForeignKey("agent_reference_file.id"), nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    reference_file = db.relationship("AgentReferenceFile")
    created_by = db.relationship("User")
    lines = db.relationship("LaborProposalLine", backref="batch", cascade="all, delete-orphan", order_by="LaborProposalLine.id")


class LaborProposalLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("labor_proposal_batch.id"), nullable=False, index=True)
    price_item_id = db.Column(db.Integer, db.ForeignKey("price_item.id", ondelete="SET NULL"), nullable=True, index=True)
    item_name = db.Column(db.String(180), nullable=False)
    unit = db.Column(db.String(40), nullable=False, default="ea")
    current_hours = db.Column(db.Float, nullable=False, default=0)
    proposed_hours = db.Column(db.Float, nullable=False, default=0)
    confidence = db.Column(db.String(20), nullable=False, default="medium")
    rationale = db.Column(db.Text, nullable=False, default="")
    source_excerpt = db.Column(db.Text, nullable=False, default="")
    approved = db.Column(db.Boolean, nullable=False, default=False)
    price_item = db.relationship("PriceItem")


class MikeMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship("User")


class ClockSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    clock_in = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    clock_out = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(255), nullable=False, default="")
    user = db.relationship("User", backref=db.backref("clock_sessions", order_by="ClockSession.clock_in.desc()"))
    visits = db.relationship("JobVisit", backref="clock_session", cascade="all, delete-orphan", order_by="JobVisit.started_at")

    @property
    def end_time(self):
        return self.clock_out or datetime.utcnow()

    @property
    def total_seconds(self):
        return max((self.end_time - self.clock_in).total_seconds(), 0)

    @property
    def visit_seconds(self):
        return sum(visit.total_seconds for visit in self.visits)

    @property
    def general_seconds(self):
        return max(self.total_seconds - self.visit_seconds, 0)


class JobVisit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clock_session_id = db.Column(db.Integer, db.ForeignKey("clock_session.id"), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(255), nullable=False, default="")
    hourly_rate = db.Column(db.Float, nullable=False, default=0)
    job = db.relationship("Job", backref=db.backref("visits", order_by="JobVisit.started_at.desc()"))

    @property
    def total_seconds(self):
        return max(((self.ended_at or datetime.utcnow()) - self.started_at).total_seconds(), 0)


TASK_STATUSES = {"todo": "To Do", "in_progress": "In Progress", "completed": "Completed"}


class WorkTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    category = db.Column(db.String(100), nullable=False, default="General")
    status = db.Column(db.String(30), nullable=False, default="todo", index=True)
    priority = db.Column(db.String(20), nullable=False, default="normal")
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    due_date = db.Column(db.Date, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    assigned_user = db.relationship("User", foreign_keys=[assigned_user_id], backref="assigned_tasks")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


BUG_STATUSES = {"open": "Open", "in_progress": "In Progress", "completed": "Completed"}
BUG_PRIORITIES = {"low": "Low", "normal": "Normal", "high": "High", "critical": "Critical"}


class BugReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    area = db.Column(db.String(100), nullable=False, default="General")
    page_url = db.Column(db.String(500), nullable=False, default="")
    priority = db.Column(db.String(20), nullable=False, default="normal", index=True)
    status = db.Column(db.String(30), nullable=False, default="open", index=True)
    resolution_notes = db.Column(db.Text, nullable=False, default="")
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class TaskAgentMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("task_agent_messages", cascade="all, delete-orphan", order_by="TaskAgentMessage.created_at"))


class Training(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    provider = db.Column(db.String(180), nullable=False, default="")
    category = db.Column(db.String(100), nullable=False, default="General")
    training_date = db.Column(db.Date, nullable=True)
    attendees = db.Column(db.String(255), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    ai_summary = db.Column(db.Text, nullable=False, default="")
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.relationship("User")
    files = db.relationship("TrainingFile", backref="training", cascade="all, delete-orphan", order_by="TrainingFile.uploaded_at")
    messages = db.relationship("TrainingMessage", backref="training", cascade="all, delete-orphan", order_by="TrainingMessage.created_at")


class TrainingFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    training_id = db.Column(db.Integer, db.ForeignKey("training.id"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False, default="")
    extracted_text = db.Column(db.Text, nullable=False, default="")
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def is_image(self):
        return self.mime_type.startswith("image/")


class TrainingMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    training_id = db.Column(db.Integer, db.ForeignKey("training.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


JOB_STATUSES = [
    ("new_request", "New request"),
    ("review_scheduled", "Site review scheduled"),
    ("working_quote", "Working up quote"),
    ("quote_scheduled", "Quote presentation scheduled"),
    ("awaiting_decision", "Awaiting client decision"),
    ("approved", "Approved — needs scheduling"),
    ("scheduled", "Work scheduled"),
    ("active", "Work in progress"),
    ("completed", "Completed"),
    ("declined", "Declined / closed"),
]
JOB_STATUS_LABELS = dict(JOB_STATUSES)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(180), nullable=False, default="")
    phone = db.Column(db.String(50), nullable=False, default="")
    address_line1 = db.Column(db.String(180), nullable=False, default="")
    address_line2 = db.Column(db.String(180), nullable=False, default="")
    city = db.Column(db.String(100), nullable=False, default="")
    state = db.Column(db.String(40), nullable=False, default="PA")
    postal_code = db.Column(db.String(20), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
    company_name = db.Column(db.String(180), nullable=False, default="")
    other_contacts = db.Column(db.Text, nullable=False, default="")       # extra phones and emails, one per line
    tags = db.Column(db.String(255), nullable=False, default="")
    lead_source = db.Column(db.String(120), nullable=False, default="")
    jobber_id = db.Column(db.String(80), nullable=False, default="", index=True)
    jobber_url = db.Column(db.String(255), nullable=False, default="")
    jobber_raw = db.Column(db.Text, nullable=False, default="")           # the whole Jobber record, as it was pulled
    jobber_synced_at = db.Column(db.DateTime, nullable=True)
    jobs = db.relationship("Job", backref="client", cascade="all, delete-orphan", order_by="Job.created_at.desc()")
    properties = db.relationship("ClientProperty", backref="client", cascade="all, delete-orphan", order_by="ClientProperty.id")

    @property
    def full_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.company_name and self.company_name != name:
            return f"{name} ({self.company_name})" if name else self.company_name
        return name

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    @property
    def primary_address(self):
        parts = [self.address_line1, self.address_line2, ", ".join(filter(None, [self.city, self.state])), self.postal_code]
        return " ".join(part for part in parts if part).strip()


class ClientProperty(db.Model):
    """A service address a client has besides the primary one (a rental, a second building)."""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    address_line1 = db.Column(db.String(180), nullable=False, default="")
    address_line2 = db.Column(db.String(180), nullable=False, default="")
    city = db.Column(db.String(100), nullable=False, default="")
    state = db.Column(db.String(40), nullable=False, default="")
    postal_code = db.Column(db.String(20), nullable=False, default="")
    jobber_id = db.Column(db.String(80), nullable=False, default="", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def text(self):
        parts = [self.address_line1, self.address_line2, ", ".join(filter(None, [self.city, self.state])), self.postal_code]
        return " ".join(part for part in parts if part).strip()


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    address_line1 = db.Column(db.String(180), nullable=False, default="")
    address_line2 = db.Column(db.String(180), nullable=False, default="")
    city = db.Column(db.String(100), nullable=False, default="")
    state = db.Column(db.String(40), nullable=False, default="PA")
    postal_code = db.Column(db.String(20), nullable=False, default="")
    status = db.Column(db.String(40), nullable=False, default="new_request", index=True)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    review_at = db.Column(db.DateTime, nullable=True)
    quote_presentation_at = db.Column(db.DateTime, nullable=True)
    work_start_date = db.Column(db.Date, nullable=True)
    duration_days = db.Column(db.Integer, nullable=False, default=1)
    internal_notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    job_number = db.Column(db.Integer, unique=True, nullable=True, index=True)
    contract_amount = db.Column(db.Float, nullable=False, default=0)
    material_cost = db.Column(db.Float, nullable=False, default=0)
    drive_minutes = db.Column(db.Float, nullable=True)
    drive_distance_miles = db.Column(db.Float, nullable=True)
    drive_checked_at = db.Column(db.DateTime, nullable=True)
    drive_error = db.Column(db.String(255), nullable=False, default="")
    drive_minutes_override = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="")          # '' made here · 'jobber' pulled from Jobber
    jobber_id = db.Column(db.String(80), nullable=False, default="", index=True)
    jobber_number = db.Column(db.String(20), nullable=False, default="")
    jobber_url = db.Column(db.String(255), nullable=False, default="")
    jobber_quote_id = db.Column(db.String(80), nullable=False, default="", index=True)
    jobber_quote_number = db.Column(db.String(20), nullable=False, default="")
    jobber_quote_status = db.Column(db.String(40), nullable=False, default="")
    jobber_quote_total = db.Column(db.Float, nullable=True)
    jobber_request_id = db.Column(db.String(80), nullable=False, default="", index=True)
    jobber_raw = db.Column(db.Text, nullable=False, default="")             # {"job":…, "quote":…, "request":…} as pulled
    jobber_synced_at = db.Column(db.DateTime, nullable=True)
    assigned_user = db.relationship("User", backref="assigned_jobs")
    quotes = db.relationship("Quote", backref="job", cascade="all, delete-orphan", order_by="Quote.created_at.desc()")
    inspections = db.relationship("OnsiteInspection", backref="job", cascade="all, delete-orphan", order_by="OnsiteInspection.updated_at.desc()")
    material_order = db.relationship("JobMaterialOrder", back_populates="job", uselist=False, cascade="all, delete-orphan")

    @property
    def status_label(self):
        return JOB_STATUS_LABELS.get(self.status, self.status.replace("_", " ").title())

    @property
    def bucket(self):
        """completed · active (quote approved) · quoted (a confirmed quote, no decision yet) · in_works (before a quote)."""
        if self.status in {"completed", "declined"}:
            return "completed"
        if self.status in {"approved", "scheduled", "active"}:
            return "active"
        if self.material_order is not None and self.material_order.quote_option_id:
            return "active"
        if any(quote.status == "confirmed" for quote in self.quotes):
            return "quoted"
        if self.jobber_quote_status in ("awaiting_response", "changes_requested"):
            return "quoted"                  # a quote sent from Jobber, waiting on the client
        return "in_works"

    @property
    def jobber(self):
        """What Jobber had for this job (line items, visits, invoices, the quote), for the job page."""
        try:
            return json.loads(self.jobber_raw) if self.jobber_raw else {}
        except ValueError:
            return {}

    @property
    def job_address(self):
        parts = [self.address_line1, self.address_line2, ", ".join(filter(None, [self.city, self.state])), self.postal_code]
        return " ".join(part for part in parts if part).strip()

    @property
    def schedule_anchor(self):
        if self.work_start_date:
            return self.work_start_date
        if self.review_at:
            return self.review_at.date()
        if self.quote_presentation_at:
            return self.quote_presentation_at.date()
        return self.created_at.date()

    @property
    def purchase_order(self):
        last = re.sub(r"[^a-z0-9]", "", self.client.last_name.casefold()) or "client"
        return f"{last}-{self.job_number or self.id}-mat"

    @property
    def labor_cost(self):
        total = 0
        for visit in self.visits:
            rate = visit.hourly_rate or visit.clock_session.user.hourly_rate or 0
            total += (visit.total_seconds / 3600) * rate
        return total

    @property
    def total_material_cost(self):
        receipt_cost = sum(expense.total for expense in self.expenses if expense.category == "Materials direct job")
        return (self.material_cost or 0) + receipt_cost


class OnsiteInspection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    findings_json = db.Column(db.Text, nullable=False, default="{}")
    observations = db.Column(db.Text, nullable=False, default="")
    client_priorities = db.Column(db.Text, nullable=False, default="")
    ai_tip = db.Column(db.Text, nullable=False, default="")
    home_age = db.Column(db.String(20), nullable=False, default="unknown")
    top_sellers_json = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = db.relationship("User")
    photos = db.relationship("OnsiteInspectionPhoto", backref="inspection", cascade="all, delete-orphan", order_by="OnsiteInspectionPhoto.uploaded_at")

    @property
    def findings(self):
        try:
            value = json.loads(self.findings_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    @property
    def checked_count(self):
        return sum(len(values) for values in self.findings.values() if isinstance(values, list))

    @property
    def top_sellers(self):
        try:
            value = json.loads(self.top_sellers_json or "[]")
            return value if isinstance(value, list) else []
        except (TypeError, json.JSONDecodeError):
            return []


class OnsiteInspectionPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey("onsite_inspection.id"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.String(255), nullable=False, default="")
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


EXPENSE_CATEGORIES = [
    "Equipment rental", "Van gas", "Van maintenance", "Materials direct job",
    "Materials general overhead", "Hotel", "Small tools general overhead",
    "Meals", "Permits / inspections", "Office supplies", "Marketing",
    "Returns/refund", "COGS subcontractor", "Subscriptions",
]


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=True, index=True)
    vendor = db.Column(db.String(180), nullable=False)
    total = db.Column(db.Float, nullable=False, default=0)
    category = db.Column(db.String(100), nullable=False)
    reimbursable = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, nullable=False, default="")
    receipt_filename = db.Column(db.String(255), nullable=True)
    expense_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    submitted_by = db.relationship("User", backref="expenses")
    job = db.relationship("Job", backref="expenses")


class QuickQuote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(180), nullable=False, default="")
    phone = db.Column(db.String(60), nullable=False, default="")
    email = db.Column(db.String(180), nullable=False, default="")
    address = db.Column(db.String(255), nullable=False, default="")
    description = db.Column(db.Text, nullable=False)
    ai_result_json = db.Column(db.Text, nullable=False, default="")
    ai_message = db.Column(db.Text, nullable=False, default="")
    option_count = db.Column(db.Integer, nullable=False, default=6)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    assigned_job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    assigned_job = db.relationship("Job", foreign_keys=[assigned_job_id])

    @property
    def ai_result(self):
        try: return json.loads(self.ai_result_json) if self.ai_result_json else {}
        except json.JSONDecodeError: return {}


QUOTE_TIERS = ["Economy", "Basic", "Bronze", "Silver", "Gold", "Platinum"]
THREE_OPTION_TIERS = ["Economy", "Gold", "Platinum"]


def quote_tiers(option_count):
    return THREE_OPTION_TIERS if int(option_count or 6) == 3 else QUOTE_TIERS


def describe_ai_error(error):
    """Return a useful, credential-safe explanation for the quoting screen."""
    error_name = error.__class__.__name__
    raw = " ".join(str(error).split())
    raw = re.sub(r"sk-[A-Za-z0-9_-]+", "[API key hidden]", raw)
    explanations = {
        "AuthenticationError": "OpenAI rejected the API key. Verify OPENAI_API_KEY on PythonAnywhere and reload the web app.",
        "RateLimitError": "OpenAI reported a rate or billing limit. Check API billing and usage, then try again.",
        "APIConnectionError": "The server could not connect to OpenAI. Check PythonAnywhere outbound access and try again.",
        "APITimeoutError": "OpenAI did not respond before the request timed out. The existing draft was left unchanged.",
        "BadRequestError": "OpenAI rejected the request format. The server error log contains the technical details.",
        "JSONDecodeError": "Caden returned an incomplete structured response. Try reworking once more; the existing draft was left unchanged.",
    }
    if error_name in explanations:
        return explanations[error_name]
    if isinstance(error, ValueError):
        return f"Caden's option plan did not pass the quote rules: {raw[:320]}"
    return f"{error_name}: {raw[:320]}" if raw else f"Unexpected {error_name}. Check the PythonAnywhere error log for details."


TOP_SELLER_OPPORTUNITIES = [
    "Whole-home surge protection (SPD)",
    "Whole-home electrical safety inspection",
    "Test all GFCI and AFCI protection",
    "Panel inspection and connection torque check",
    "Smoke and carbon-monoxide detector review",
    "Check smoke detectors 10+ years old",
    "Grounding and bonding review",
    "Panel labeling and circuit identification",
]

DEFAULT_CADEN_PROMPT = """Build homeowner proposals from the supplied Power House Service Library and Pricing Sheet Library.

Create exactly the option count and tier order supplied by the application. A six-option quote uses Economy, Basic, Bronze, Silver, Gold, Platinum. A three-option quote uses Economy, Gold, Platinum.

Every tier must be meaningfully different from the tier immediately below it. As tiers increase, add at least one useful scope item, upgrade product or workmanship quality, improve protection, add convenience, improve appearance, or add longer-term value. Never repeat the same option with only a different title or price. Every option must remain safe and coherent; do not make Basic artificially unsafe or incomplete.

Use polished, plain English that an everyday homeowner understands. Bullets must explain the visible work and client benefit. Avoid unexplained trade jargon. Never invent a warranty, permit promise, code violation, product specification, service, item, ID, or price.

Pricing is deterministic. Prefer a complete Service when one matches because Services may include labor, materials, tools, and scope. Select the exact service ID and a multiplier. Carefully read the example quantity: if a service covers one light bulb and the job needs four, use multiplier 4; if a service covers ten receptacles and the job needs twenty, use multiplier 2.

PRICING REPAIR AND LABOR MEMORY:
For a simple component that does not require a complete service, select an exact Pricing Sheet item ID and quantity. Use per-foot quantities for cable or conduit when appropriate. Pricing Sheet items include material price and may include saved labor hours per unit. Use those values exactly; do not invent either value.

The always-visible pricing repair box may contain several components for one turnkey installation. Match every stated component and quantity. Apply one repair across every option containing the same missing work. When an administrator teaches you labor during quote repair (for example, “a receptacle takes 0.5 hours” or “12/2 takes 0.05 hours per foot”), map that statement to the exact Pricing Sheet item and save it as labor hours per unit. Reuse that stored labor estimate in future quotes. Clearly identify anything that cannot be matched instead of guessing.

Respect explicit pricing-repair mappings stated in the conversation. If no Service or Pricing Sheet item matches, still build every selected option and list the unresolved work under missing services. Do not invent a price.

Use the saved onsite inspection as verified job context. Never claim an unchecked condition was observed. Let the client's stated priorities influence how you explain benefits.

Ask no more than one short follow-up question, and only when one missing fact materially changes safety or the entire scope. Otherwise proceed using clear, conservative assumptions. Never pressure the client toward the most expensive option. Power House values service, safety, honesty, and long-term relationships over maximizing the sale."""

CADEN_LABOR_PROMPT_ADDENDUM = """PRICING REPAIR AND LABOR MEMORY:
The always-visible pricing repair box may contain several components for one turnkey installation. Match every stated component and quantity to exact existing Service or Pricing Sheet IDs. Pricing Sheet items include material price and labor hours per unit. Use per-foot quantities for cable or conduit. Apply one repair across every option containing the same missing work.

When an administrator teaches you labor during quote repair—for example, “a receptacle takes 0.5 hours” or “12/2 takes 0.05 hours per foot”—save it on the exact Pricing Sheet item as labor hours per unit and reuse it in future quotes. Never invent a price, quantity, labor estimate, or catalog match. Clearly identify anything that remains unavailable."""

CADEN_MANUAL_EDIT_ADDENDUM = """MANUAL QUOTE EDITS:
The administrator may directly edit option titles, descriptions, bullet points, quantities, material prices, and labor hours in the live draft. Treat those edits as authoritative. If asked to rework the quote afterward, preserve the stated scope, quantities, pricing mappings, and labor decisions unless the administrator explicitly asks to replace them. Improve organization and homeowner-facing wording without silently undoing manual corrections."""

CADEN_OPTION_STRATEGY_ADDENDUM = """OPTION STRATEGY, INCLUDED LABOR, AND SALES OPPORTUNITIES:
Create exactly the number of options selected in the quote—either three or six—and use only the tier names supplied by the application. The homeowner's original request is not automatically Basic. It may belong in the middle or top option. When safe and sensible, create a less-expensive lower alternative that still solves a real need—for example, replacing sound light bulbs instead of replacing complete fixtures. Never make a lower option unsafe, deceptive, or incomplete for its stated scope.

Normal installation labor, setup, cleanup, and disposal of replaced material are already included in Power House labor pricing. Do not create separate priced lines or client-facing bullets for “labor to perform work,” routine cleanup, ordinary setup, travel, or disposal unless the administrator explicitly identifies an unusual standalone charge. Price work as material quantity plus saved labor hours multiplied by the company labor rate.

Use verified onsite photos, home-age selection, top-seller opportunities, and checklist findings as context. A top-seller selection is an opportunity to consider for an added tier, not proof that the work is required. Never claim an unseen defect or pressure the homeowner.

Manual quote outcome uploads are evidence of where the first AI draft needed correction. Compare those outcomes with the original AI snapshot, learn the documented pattern, and follow administrator-authored knowledge and manual corrections over generic assumptions."""

CADEN_DIRECT_PRICING_ADDENDUM = """DIRECT USER-SUPPLIED PRICING:
An administrator may explicitly supply a material price and labor estimate during quote chat even when the item is absent from Services and the Pricing Sheet. That is authoritative quote input, not missing pricing. Use it immediately as a direct-priced line, include the requested quantity, and produce the complete quote. Never respond that pricing is unavailable after the administrator supplied it.

Only use direct pricing when the exact supporting user message is included as source_message and it states the material price. Never invent or estimate a price. If labor hours are stated, preserve them exactly; otherwise use zero labor hours and clearly retain only the supplied material price. Set create_in_price_sheet true only when the administrator explicitly asks to add, create, or save that item in the Pricing Sheet or catalog. A direct quote price alone must not silently change the central Pricing Sheet."""

CADEN_PRESENTATION_WORDING_V1 = """CLIENT PRESENTATION WORDING:
Every client-facing option bullet must be concise, professional, and easy for a homeowner to understand. Prefer short scope phrases such as “Premium installation of …”, “Premium replacement of …”, “Premium upgrade of …”, or “Professional inspection of …”. Describe what will be completed, not the internal estimating method. Do not write paragraphs, technical explanations, sales hype, or separate bullets for ordinary labor, setup, cleanup, travel, and disposal. Aim for one clear action per bullet and generally 4–12 words."""

CADEN_CLIENT_WORDING_ADDENDUM = """CLIENT WORDING (plain language):
Write for a homeowner, not an electrician. Titles, descriptions, bullets and the introduction must be short and plain.

BULLETS — one bullet is one thing the client gets, 3–10 words, no period, no prices, part numbers, wire sizes or amperage (unless the client asked about them).
Start a bullet with a scope phrase only where it reads naturally:
- "Turnkey installation of …" — a complete new install we supply, wire and finish (an EV charger, a generator hookup, a new circuit, new fixtures).
- "Premium installation of …" — the upgraded product or higher-quality version offered in a higher tier.
- "Replacement of …" or "Premium replacement of …" — swapping something old for something new.
- "Upgrade of …" — improving what is already there (a panel, a service).
- "Safety inspection of …" — checking and testing.
Never force one of these phrases where it reads wrong. Permits, add-ons and protections are named plainly: "Electrical permit and township inspection", "Surge protection for the whole home", "Child-safe receptacles throughout the kitchen".

Name every part by what it does for the homeowner, never by a trade term or acronym:
GFCI receptacle → water-protection receptacle · GFCI breaker → water-protection breaker · AFCI breaker → fire-protection breaker ·
dual-function (AFCI/GFCI) breaker → fire- and water-protection breaker · SPD / surge protective device → whole-home surge protector ·
tamper-resistant receptacle → child-safe receptacle · weather-resistant receptacle with in-use cover → weatherproof outdoor receptacle ·
Romex / NM cable / 12-2 → new wiring · load center / panelboard → electrical panel · meter base / meter socket → electric meter housing ·
service entrance cable (SER/SEU) → main power feed · generator interlock kit → generator safety switch · EV/AC disconnect → outdoor shut-off switch ·
recessed cans / wafers → recessed ceiling lights / slim LED ceiling lights · smoke/CO detectors → smoke and carbon-monoxide alarms ·
ground rods / grounding electrode / bonding → grounding system.
For anything else ask: what would the homeowner call it, and what does it do for them?

Never write bullets for labor, setup, cleanup, travel, disposal, "materials" or testing on its own; they are always included.
When a higher tier upgrades something a lower tier also has, word it to show the upgrade ("Premium installation of designer LED fixtures" above "Turnkey installation of LED fixtures").

TITLES AND TEXT
- Option title: 2–5 words naming the outcome ("Essential outlet repair", "Kitchen safety upgrade", "Complete panel upgrade"). Not the tier name, not a sentence.
- Option description: one sentence under 25 words saying what the homeowner ends up with.
- Introduction: one or two short, friendly sentences. No jargon, no pressure.
- The message to the office: brief; say what you assumed and anything you could not price.

Before you answer, reread every bullet as the homeowner: no acronyms or trade names left, no scope phrase where it reads wrong, no bullet for labor or cleanup, and each higher tier clearly better than the one below."""

ONSITE_CHECKLIST = [
    ("Attic / Crawl Space", [
        "Spare circuits for future expansion", "Service receptacles", "Recessed lighting for future finishing",
        "Entrance-controlled lighting", "Observation camera", "Flood sensors", "Wire management or recessing",
        "Dedicated appliance or equipment circuits", "Rodent-resistant wiring", "Ventilation upgrade", "Whole-home fan upgrade",
    ]),
    ("Basement / Storage / Utility", [
        "Spare circuits for future finishing", "Lighting upgrade", "Consolidated lighting controls", "Additional receptacles",
        "Dedicated circuits for major equipment", "Water or flood sensors", "Additional smoke or carbon-monoxide detection",
        "Main-panel relocation", "Main-panel restoration", "Whole-home surge protection", "Power-factor protection",
        "Service receptacle near equipment", "Battery-backup panel lighting", "Generator interlock or manual transfer equipment",
        "Automatic transfer equipment", "Full service identification", "Load balancing", "Arc-fault protection",
        "Raised receptacle and dehumidifier connection",
    ]),
    ("Bathrooms", [
        "Ventilation upgrade", "Heat lamp or fan", "Shower, ceiling, or vanity lighting", "Additional receptacles",
        "Ventilation motion control", "Dedicated circuit", "Bidet circuit", "Localized GFCI protection", "Heated-floor power",
    ]),
    ("Bedrooms", [
        "Ceiling fan", "Lighting upgrade", "TV wall mount and power", "Cord management", "Bedside receptacles",
        "Lighting switch controls", "Bedside three-way lighting control", "Furniture or bed underglow", "USB charging receptacles",
        "Closet lighting", "Fandelier", "Fixture escutcheons", "Speakers", "Dedicated safe power", "Camera and wall monitor",
        "Exterior-light smart controls",
    ]),
    ("Hallways", [
        "Night-activated pathway receptacles", "Thermostat upgrade", "Attic light-on indicator", "Vacuum or appliance receptacles",
        "Whole-home fan control upgrade", "Hallway lighting upgrade",
    ]),
    ("Home Office", [
        "Electronics surge protection", "Cord management", "Desk-height receptacles", "Dimmable LED task lighting",
        "Ceiling or wall fan", "Dedicated AC or equipment circuit", "Conference or recording lighting", "Speaker system",
        "USB charging receptacles", "Camera system and monitor", "Generator connection", "Computer UPS",
        "Specialty ventilation", "TV mounting", "Dedicated desk-area heat", "Decor underlighting",
    ]),
    ("Kitchen", [
        "Under-cabinet appliance circuits", "Dishwasher or appliance disconnects", "Localized GFCI protection",
        "Ventilation or smoke-removal upgrade", "Smoke-detector relocation", "Recessed or trim lighting upgrade",
        "Chandelier cleaning or service", "Under-cabinet lighting", "Refrigerator outage notification",
        "Countertop USB charging", "Dedicated circuit for overloaded appliances", "Island power", "TV receptacle", "Speaker system",
    ]),
    ("Living Room", [
        "Floor receptacles", "GFCI protection for floor power", "Raised receptacles behind furniture",
        "Entertainment wiring behind TV", "Surge-protected TV receptacle", "Cord management", "Ceiling fan or recessed lighting",
        "Speaker lighting or speaker system", "Replace switched receptacles with lighting controls", "TV mounting", "Accent lighting",
    ]),
]


class Quote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="draft", index=True)
    revision = db.Column(db.Integer, nullable=False, default=1)
    title = db.Column(db.String(180), nullable=False, default="Service Options")
    introduction = db.Column(db.Text, nullable=False, default="")
    option_count = db.Column(db.Integer, nullable=False, default=6)
    ai_snapshot_json = db.Column(db.Text, nullable=False, default="")
    travel_minutes_snapshot = db.Column(db.Float, nullable=False, default=0)
    travel_cost_snapshot = db.Column(db.Float, nullable=False, default=0)
    travel_origin_snapshot = db.Column(db.String(255), nullable=False, default="")
    travel_destination_snapshot = db.Column(db.String(255), nullable=False, default="")
    travel_method_snapshot = db.Column(db.String(40), nullable=False, default="estimated")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    pricing_needed_json = db.Column(db.Text, nullable=False, default="")    # Caden's suggested prices for unpriced items
    created_by = db.relationship("User", backref="created_quotes")
    messages = db.relationship("QuoteMessage", backref="quote", cascade="all, delete-orphan", order_by="QuoteMessage.created_at")
    options = db.relationship("QuoteOption", backref="quote", cascade="all, delete-orphan", order_by="QuoteOption.position")
    manual_outcomes = db.relationship("ManualQuoteUpload", backref="quote", cascade="all, delete-orphan", order_by="ManualQuoteUpload.created_at.desc()")

    @property
    def has_missing_pricing(self):
        return any(option.has_missing_price for option in self.options)

    @property
    def pricing_needed(self):
        try:
            value = json.loads(self.pricing_needed_json or "{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}


class QuoteMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("quote.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ManualQuoteUpload(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("quote.id"), nullable=False, index=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    extracted_text = db.Column(db.Text, nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    ai_snapshot_json = db.Column(db.Text, nullable=False, default="")
    final_snapshot_json = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by = db.relationship("User")

    @property
    def ai_snapshot(self):
        try: return json.loads(self.ai_snapshot_json) if self.ai_snapshot_json else {}
        except (TypeError, json.JSONDecodeError): return {}

    @property
    def final_snapshot(self):
        try: return json.loads(self.final_snapshot_json) if self.final_snapshot_json else {}
        except (TypeError, json.JSONDecodeError): return {}


class QuoteOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("quote.id"), nullable=False, index=True)
    tier = db.Column(db.String(30), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    bullets_json = db.Column(db.Text, nullable=False, default="[]")
    position = db.Column(db.Integer, nullable=False, default=0)
    manually_edited = db.Column(db.Boolean, nullable=False, default=False)
    manual_total_override = db.Column(db.Float, nullable=True)
    lines = db.relationship("QuoteOptionService", backref="option", cascade="all, delete-orphan", order_by="QuoteOptionService.position")

    @property
    def bullets(self):
        try:
            return json.loads(self.bullets_json)
        except (TypeError, json.JSONDecodeError):
            return []

    @property
    def total(self):
        if self.manual_total_override is not None:
            return self.manual_total_override
        return sum(line.total for line in self.lines) + (self.quote.travel_cost_snapshot or 0)

    @property
    def material_total(self):
        return sum(line.material_total for line in self.lines)

    @property
    def labor_hours(self):
        return sum(line.labor_hours for line in self.lines) + ((self.quote.travel_minutes_snapshot or 0) / 60 if self.quote.travel_cost_snapshot else 0)

    @property
    def labor_total(self):
        return sum(line.labor_total for line in self.lines) + (self.quote.travel_cost_snapshot or 0)

    @property
    def has_missing_price(self):
        return any(line.is_missing for line in self.lines)


class QuoteOptionService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    option_id = db.Column(db.Integer, db.ForeignKey("quote_option.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id", ondelete="SET NULL"), nullable=True, index=True)
    price_item_id = db.Column(db.Integer, db.ForeignKey("price_item.id", ondelete="SET NULL"), nullable=True, index=True)
    service_name = db.Column(db.String(180), nullable=False)
    example_quantity = db.Column(db.String(80), nullable=False, default="1")
    multiplier = db.Column(db.Float, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False, default=0)
    material_unit_price = db.Column(db.Float, nullable=True)
    labor_hours_per_unit = db.Column(db.Float, nullable=False, default=0)
    labor_rate_snapshot = db.Column(db.Float, nullable=False, default=0)
    manual_priced = db.Column(db.Boolean, nullable=False, default=False)
    manually_edited = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    service = db.relationship("Service")
    price_item = db.relationship("PriceItem", back_populates="quote_lines")

    @property
    def is_missing(self):
        return self.service_id is None and self.price_item_id is None and not self.manual_priced

    @property
    def source_label(self):
        if self.service_id:
            return "Service"
        if self.price_item_id:
            return "Pricing item"
        if self.manual_priced:
            return "Manual quote line"
        return "Missing"

    @property
    def total(self):
        if self.material_unit_price is None:
            return (self.multiplier or 0) * (self.unit_price or 0)
        return self.material_total + self.labor_total

    @property
    def material_total(self):
        unit_material = self.unit_price if self.material_unit_price is None else self.material_unit_price
        return (self.multiplier or 0) * (unit_material or 0)

    @property
    def labor_hours(self):
        return (self.multiplier or 0) * (self.labor_hours_per_unit or 0)

    @property
    def labor_total(self):
        return self.labor_hours * (self.labor_rate_snapshot or 0)


class JobMaterialOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, unique=True, index=True)
    quote_option_id = db.Column(db.Integer, db.ForeignKey("quote_option.id", ondelete="SET NULL"), nullable=True, index=True)
    selected_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="draft", index=True)
    selected_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    cart_ready_at = db.Column(db.DateTime, nullable=True)
    job = db.relationship("Job", back_populates="material_order")
    quote_option = db.relationship("QuoteOption")
    selected_by = db.relationship("User")
    items = db.relationship("JobMaterialOrderItem", back_populates="order", cascade="all, delete-orphan", order_by="JobMaterialOrderItem.position")

    @property
    def material_total(self):
        return sum(item.total for item in self.items)


class JobMaterialOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("job_material_order.id"), nullable=False, index=True)
    price_item_id = db.Column(db.Integer, db.ForeignKey("price_item.id", ondelete="SET NULL"), nullable=True, index=True)
    name = db.Column(db.String(180), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit = db.Column(db.String(40), nullable=False, default="ea")
    unit_price = db.Column(db.Float, nullable=False, default=0)
    supplier = db.Column(db.String(100), nullable=False, default="")
    part_number = db.Column(db.String(120), nullable=False, default="")
    source_services = db.Column(db.Text, nullable=False, default="")
    notes = db.Column(db.String(255), nullable=False, default="")
    position = db.Column(db.Integer, nullable=False, default=0)
    order = db.relationship("JobMaterialOrder", back_populates="items")
    price_item = db.relationship("PriceItem")

    @property
    def total(self):
        return (self.quantity or 0) * (self.unit_price or 0)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.can(permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.context_processor
def inject_access_helpers():
    return {"can": lambda permission: current_user.is_authenticated and current_user.can(permission)}


# ---------------- Lightweight CSRF ----------------
# Small internal team tool with only a couple of users — this is a minimal
# same-origin token check rather than a full CSRF library.

def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def check_csrf():
    token = session.get("csrf_token")
    submitted = request.form.get("csrf_token")
    if not token or not submitted or not secrets.compare_digest(token, submitted):
        abort(400, description="Your session expired — please try again.")


app.jinja_env.globals["csrf_token"] = get_csrf_token


# A page's fetch() asks for JSON. When it gets the login page or an error page instead, the button can only say
# 'Unexpected token "<"'. So a request that asks for JSON is always answered in JSON, with the reason in words.
def wants_json():
    return request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With") == "fetch"


@login_manager.unauthorized_handler
def json_aware_unauthorized():
    if wants_json():
        return jsonify({"error": "You were signed out. Refresh the page, sign in, and try again.", "signed_out": True}), 401
    flash(login_manager.login_message, "message")                 # what Flask-Login does by itself
    return redirect(login_url(login_manager.login_view, request.url))


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
def json_aware_http_error(error):
    if wants_json():
        message = {400: "This page is out of date (your session expired). Refresh the page and try again.",
                   403: "Your role does not allow this.", 404: "That record no longer exists. Refresh the page.",
                   405: "The site could not take that request. Refresh the page and try again."}[error.code]
        return jsonify({"error": message}), error.code
    return error


@app.errorhandler(500)
def json_aware_server_error(error):
    original = getattr(error, "original_exception", None) or error
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        pass
    if wants_json():
        return jsonify({"error": "The site hit an error (%s). Try again; if it keeps happening, report it from Bug Reports." % original.__class__.__name__}), 500
    return error


# ---------------- Local Cooper updater API ----------------

LOCAL_COOPER_TOKEN_KEY = "local_cooper_updater_token_hash"


def local_cooper_api_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        setting = db.session.get(CompanySetting, LOCAL_COOPER_TOKEN_KEY)
        if not token or setting is None or not check_password_hash(setting.value, token):
            return jsonify({"error": "A valid Local Cooper Updater token is required."}), 401
        return view(*args, **kwargs)
    return wrapped


def normalize_pricing_unit(value):
    normalized = re.sub(r"[^a-z]", "", str(value or "").casefold())
    aliases = {
        "ft": "ft", "foot": "ft", "feet": "ft",
        "ea": "ea", "each": "ea", "piece": "ea", "pieces": "ea", "pc": "ea",
        "box": "box", "roll": "roll", "lb": "lb", "pound": "lb", "job": "job",
    }
    return aliases.get(normalized, normalized)


def calculate_local_cooper_price(price_item, source_price, source_quantity, source_unit):
    try:
        source_price = float(source_price)
        source_quantity = float(source_quantity or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("Cooper returned an invalid price or package quantity.") from exc
    if source_price < 0 or source_quantity <= 0:
        raise ValueError("Cooper price must be non-negative and package quantity must be greater than zero.")

    requested_unit = normalize_pricing_unit(price_item.unit)
    source_unit = normalize_pricing_unit(source_unit)
    if requested_unit not in {"ft", "ea", "box", "roll", "lb", "job"}:
        raise ValueError(f"Unsupported Pricing Sheet unit '{price_item.unit}'.")
    if not source_unit:
        raise ValueError("Cooper did not identify the source pricing unit.")
    if source_unit != requested_unit:
        raise ValueError(
            f"Unit mismatch: Pricing Sheet uses '{requested_unit}', but Cooper returned '{source_unit}'."
        )

    calculated = source_price / source_quantity
    if source_quantity == 1:
        message = f"Local Cooper scan found ${source_price:,.2f} per {source_unit}."
    else:
        message = (
            f"Local Cooper scan: ${source_price:,.2f} per {source_quantity:,.0f} {source_unit} "
            f"→ ${calculated:,.5f} per {requested_unit}."
        )
    return calculated, message


@app.template_filter("map_price_items")
def map_price_items(items):
    return [{
        "id": item.id,
        "name": item.name,
        "unit": item.unit,
        "price": item.price,
        "labor_hours_per_unit": item.labor_hours_per_unit,
        "cooper_part_number": item.cooper_part_number,
        "supplier_parts": [{"supplier": row.supplier, "part_number": row.part_number} for row in item.supplier_parts],
        "category": item.category,
    } for item in items]


@app.template_filter("map_tools")
def map_tools(items):
    return [{"id": item.id, "name": item.name, "tool_type": item.tool_type, "rental_cost": item.rental_cost, "notes": item.notes} for item in items]


# Client wording: trade terms become what the part does for the homeowner. Applied to everything a client reads,
# whatever wrote it (Caden, a manual edit, an older quote). Order matters: the most specific phrases first.
CLIENT_TERMS = [
    (r"\b(?:dual[- ]function|AFCI\s*/\s*GFCI|GFCI\s*/\s*AFCI|combination AFCI/GFCI)\s+breakers?\b", "fire- and water-protection breaker", True),
    (r"\b(?:GFCI|GFI|ground[- ]fault(?: circuit interrupter)?)\s+breakers?\b", "water-protection breaker", True),
    (r"\b(?:AFCI|arc[- ]fault(?: circuit interrupter)?)\s+breakers?\b", "fire-protection breaker", True),
    (r"\b(?:GFCI|GFI|ground[- ]fault(?: circuit interrupter)?)\s+(?:receptacles?|outlets?|plugs?|devices?)\b", "water-protection receptacle", True),
    (r"\b(?:GFCI|GFI|ground[- ]fault)\s+protect(?:ion|ed)\b", "water protection", False),
    (r"\b(?:AFCI|arc[- ]fault)\s+protect(?:ion|ed)\b", "fire protection", False),
    (r"\b(?:GFCI|GFI)s?\b", "water-protection receptacle", True),
    (r"\b(?:AFCI)s?\b", "fire-protection breaker", True),
    (r"\b(?:whole[- ](?:home|house)\s+)?(?:surge protective devices?|SPDs?|type\s*[12]\s+surge (?:protectors?|devices?))\b", "whole-home surge protector", True),
    (r"\b(?:TR|tamper[- ]resistant)\s+(?:receptacles?|outlets?)\b", "child-safe receptacle", True),
    (r"\b(?:WR|weather[- ]resistant)\s+(?:receptacles?|outlets?)(?:\s+with\s+(?:an?\s+)?(?:in[- ]use|bubble)\s+covers?)?\b", "weatherproof outdoor receptacle", True),
    (r"\b(?:in[- ]use|bubble)\s+covers?\b", "weatherproof cover", True),
    (r"\b\d{1,2}\s*[/-]\s*\d\s*(?:w/?\s*g(?:round)?\s*)?(?:romex|nm-?b?|nm cable|cable|wire)\b", "new wiring", False),
    (r"\b(?:romex|nm-?b cable|nm cable)\b", "wiring", False),
    (r"\b(?:load\s*cent(?:er|re)s?|panelboards?|breaker box(?:es)?)\b", "electrical panel", True),
    (r"\bmeter (?:base|socket|can)s?\b", "electric meter housing", True),
    (r"\b(?:service entrance cable|SER cable|SEU cable|SER|SEU)\b", "main power feed", False),
    (r"\b(?:generator\s+)?interlock(?:\s+kit)?s?\b", "generator safety switch", True),
    (r"\b(?:EV|AC|A/C|fused|non[- ]fused|outdoor)\s+disconnects?\b", "outdoor shut-off switch", True),
    (r"\brecessed cans?\b|\bcan lights?\b", "recessed ceiling lights", False),
    (r"\b(?:LED\s+)?wafer lights?\b|\bwafers\b", "slim LED ceiling lights", False),
    (r"\bsmoke/CO detectors?\b|\bsmoke and CO detectors?\b", "smoke and carbon-monoxide alarms", False),
    (r"\bCO detectors?\b", "carbon-monoxide alarm", True),
    (r"\bsmoke detectors?\b", "smoke alarm", True),
    (r"\b(?:ground(?:ing)? rods?|grounding electrodes?(?: system)?)\b", "grounding system", False),
    (r"\bEMT\b", "metal conduit", False),
]
_CLIENT_TERMS = [(re.compile(pattern, re.IGNORECASE), words, plural) for pattern, words, plural in CLIENT_TERMS]


def plain_terms(text):
    """Replace trade terms with plain words. Keeps plurals plural ("GFCI outlets" → "water-protection receptacles")."""
    text = str(text or "")
    for pattern, words, can_be_plural in _CLIENT_TERMS:
        def swap(match, words=words, can_be_plural=can_be_plural):
            found = match.group(0)
            plural = can_be_plural and re.search(r"(?:s|es)$", found.split()[-1], re.IGNORECASE) and not found.lower().endswith(("ss", "us"))
            result = words + ("s" if plural and not words.endswith("s") else "")
            return result[0].upper() + result[1:] if found[:1].isupper() and match.start() == 0 else result
        text = pattern.sub(swap, text)
    text = re.sub(r"\b(new )new\b", r"\1", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


SCOPE_VERBS = (
    (r"^(?:install(?:ation)?(?:\s+of)?|installing)\s+", "Turnkey installation of "),
    (r"^(?:replace(?:ment)?(?:\s+of)?|replacing|swap(?:\s+out)?)\s+", "Replacement of "),
    (r"^(?:upgrade(?:\s+of)?|upgrading)\s+", "Upgrade of "),
    (r"^(?:inspect(?:ion)?(?:\s+of)?|check(?:ing)?|test(?:ing)?)\s+", "Safety inspection of "),
    (r"^(?:provide|supply|furnish|add|run|wire)\s+", "Turnkey installation of "),
)


def professionalize_scope_bullet(value):
    """A short, plain, homeowner-facing scope line. A bullet that starts with an action ("Install …", "Replace …")
    becomes a scope phrase ("Turnkey installation of …", "Replacement of …"); Caden chooses "Premium …" himself for
    an upgraded product. Anything else (a permit, a protection, an add-on) is
    left as it is. Trade terms become plain words."""
    scope = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-•.;:")
    if not scope:
        return ""
    scope = re.sub(r"^(?:we\s+will|we'll|our\s+team\s+will|power\s*house\s+will)\s+", "", scope, flags=re.IGNORECASE)
    scope = re.split(r"[;]|(?<=[.!?])\s+", scope, maxsplit=1)[0].strip(" .")
    if not re.match(r"^(?:premium|turnkey|professional|safety|replacement\s+of|upgrade\s+of)\b", scope, flags=re.IGNORECASE):
        for pattern, prefix in SCOPE_VERBS:
            if re.match(pattern, scope, flags=re.IGNORECASE):
                scope = prefix + re.sub(pattern, "", scope, count=1, flags=re.IGNORECASE).strip()
                break
    result = plain_terms(scope)
    result = re.sub(r"\b(installation|replacement|upgrade|inspection) of (?:the |a |an )?new\b", r"\1 of new", result, flags=re.IGNORECASE)
    result = result[:1].upper() + result[1:]
    if len(result) > 125:
        shortened = result[:125].rsplit(" ", 1)[0].rstrip(" ,.-")
        result = shortened if shortened else result[:125]
    return result.rstrip(" .")


app.jinja_env.filters["presentation_scope"] = professionalize_scope_bullet
app.jinja_env.filters["plain_terms"] = plain_terms


# ---------------- Setup gate ----------------
# If there are no users yet, every request is routed to /setup to create
# the first account instead of showing a login form nobody can use.

@app.before_request
def require_setup_if_no_users():
    if request.endpoint in ("setup", "static"):
        return
    if User.query.count() == 0:
        return redirect(url_for("setup"))


@app.before_request
def enforce_role_permissions():
    if not current_user.is_authenticated or request.endpoint in {"static", "logout", "dashboard"}:
        return
    admin_endpoints = {"settings", "update_routing_settings", "settings_bug_reports", "create_bug_report", "update_bug_report", "tools_library", "create_tool", "edit_tool", "delete_tool", "generate_local_cooper_token", "revoke_local_cooper_token", "roles", "update_role", "create_agent", "edit_agent", "add_agent_knowledge", "edit_agent_knowledge", "delete_agent_knowledge", "upload_agent_file", "delete_agent_file", "add_user", "update_user", "reset_password", "delete_user", "releases_page", "releases_status", "releases_connect", "releases_disconnect", "releases_check", "releases_deploy", "releases_restore", "releases_auto", "flow_page", "flow_save", "jobber_page", "jobber_keys", "jobber_connect", "jobber_callback", "jobber_disconnect", "jobber_pull", "jobber_status", "jobber_import", "download_cooper_listener"}
    if request.endpoint in admin_endpoints and not (current_user.role and current_user.role.is_admin):
        abort(403)
    permission_by_endpoint = {
        "pricing_sheet":"view_pricing", "labor_calculator":"view_pricing", "upload_labor_reference":"view_pricing", "apply_labor_proposals":"view_pricing", "ask_mike":"view_pricing", "check_cooper_prices":"view_pricing", "cancel_cooper_price_sync":"view_pricing", "cooper_sync_status":"view_pricing", "cooper_sync_preview":"view_pricing", "apply_cooper_prices":"view_pricing", "update_labor_rate":"view_pricing", "create_price_item":"view_pricing", "edit_price_item":"view_pricing", "delete_price_item":"view_pricing", "cleanup_duplicate_price_items":"view_pricing", "upload_price_item_image":"view_pricing", "remove_selected_price_items":"view_pricing", "start_cooper_catalog":"view_pricing", "unconfirmed_switch":"view_pricing", "move_selected_price_items":"view_pricing",
        "services":"view_services", "create_service":"view_services", "service_detail":"view_services", "service_order_checklist":"view_services", "edit_service":"view_services", "delete_service":"view_services",
        "generate_quote":"view_quoting", "quote_assistant":"view_quoting", "quote_presentation":"view_quoting", "reopen_quote":"view_quoting", "edit_saved_quote":"view_quoting", "create_quick_quote":"view_quoting", "assign_quick_quote":"view_quoting", "set_quote_option_count":"view_quoting", "upload_manual_quote_outcome":"view_quoting", "manual_quote_file":"view_quoting", "repair_quote_missing":"view_quoting", "ai_repair_quote_pricing":"view_quoting", "edit_quote_option":"view_quoting", "rework_quote":"view_quoting", "use_quote_prices":"view_quoting",
        "clients":"view_clients", "create_client":"view_clients", "client_detail":"view_clients", "edit_client":"view_clients", "delete_client":"view_clients",
        "jobs":"view_jobs", "create_job":"view_jobs", "job_detail":"view_jobs", "edit_job":"view_jobs", "delete_job":"view_jobs", "select_job_quote_option":"view_jobs", "job_material_order":"view_jobs", "update_job_material_order":"view_jobs", "add_job_material_order_item":"view_jobs", "add_job_material_order_to_cart":"view_jobs", "schedule":"view_schedule",
        "time_tracking":"view_time", "clock_in":"view_time", "clock_out":"view_time", "start_job_visit":"view_time", "stop_job_visit":"view_time", "timesheet":"view_time", "tasks":"view_tasks", "create_task":"view_tasks", "update_task_status":"view_tasks", "ask_brendon":"view_tasks", "trainings":"view_training", "create_training":"view_training", "edit_training":"view_training", "training_detail":"view_training",
        "finance_dashboard":"view_finance", "expenses":"view_expenses", "create_expense":"view_expenses", "expense_receipt":"view_expenses",
        "onsite_checklist":"view_sales", "onsite_checklist_job":"view_sales", "save_onsite_checklist":"view_sales", "refresh_inspection_tip":"view_sales", "inspection_photo":"view_sales", "delete_inspection_photo":"view_sales", "sales_reviews":"view_sales",
    }
    permission = permission_by_endpoint.get(request.endpoint)
    if permission and not current_user.can(permission):
        abort(403)


# ---------------- Routes ----------------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if User.query.count() > 0:
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        error = validate_new_account(username, password, confirm)
        if error:
            flash(error, "error")
            return render_template("setup.html", username=username)

        admin_role = Role.query.filter_by(name="Admin").first()
        user = User(username=username, role=admin_role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Account created. Welcome to Power House Electrical Solutions.", "success")
        return redirect(url_for("dashboard"))

    return render_template("setup.html", username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("dashboard"))

        flash("Incorrect username or password.", "error")
        return render_template("login.html", username=username)

    return render_template("login.html", username="")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", service_count=Service.query.count())


@app.route("/quoting/services")
@login_required
def services():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    query = Service.query
    if search:
        query = query.filter(db.or_(Service.name.ilike(f"%{search}%"), Service.description.ilike(f"%{search}%")))
    if category:
        query = query.filter_by(category=category)
    service_list = query.order_by(Service.category, Service.name).all()
    categories = [row[0] for row in db.session.query(Service.category).distinct().order_by(Service.category).all()]
    return render_template("services.html", services=service_list, categories=categories, search=search, selected_category=category)


@app.route("/quoting/pricing")
@login_required
def pricing_sheet():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    supplier = request.args.get("supplier", "").strip()
    unit = request.args.get("unit", "").strip()
    sort = request.args.get("sort", "name")
    direction = request.args.get("direction", "asc")
    review_duplicates = request.args.get("duplicates") == "1"
    query = PriceItem.query.filter_by(active=True)
    if search:
        query = query.filter(db.or_(PriceItem.name.ilike(f"%{search}%"), PriceItem.cooper_part_number.ilike(f"%{search}%"), PriceItem.supplier_parts.any(db.or_(SupplierPartNumber.supplier.ilike(f"%{search}%"), SupplierPartNumber.part_number.ilike(f"%{search}%")))))
    if category:
        query = query.filter_by(category=category)
    if supplier:
        query = query.filter(PriceItem.supplier_parts.any(SupplierPartNumber.supplier == supplier))
    if unit:
        query = query.filter_by(unit=unit)
    unconfirmed_count = PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.category == UNCONFIRMED).count()
    if category != UNCONFIRMED and not search:
        query = query.filter(PriceItem.category != UNCONFIRMED)       # thousands of catalog items stay out of the way
    items = query.all()
    sort_keys = {
        "name": lambda item: canonical_item_name(item.name),
        "category": lambda item: (canonical_item_name(item.category), canonical_item_name(item.name)),
        "supplier": lambda item: (canonical_item_name(item.supplier_parts[0].supplier if item.supplier_parts else ""), canonical_item_name(item.name)),
        "unit": lambda item: (item.unit, canonical_item_name(item.name)),
        "price": lambda item: (item.price, canonical_item_name(item.name)),
        "labor": lambda item: (item.labor_hours_per_unit, canonical_item_name(item.name)),
        "usage": lambda item: (len(item.service_links), canonical_item_name(item.name)),
    }
    sort = sort if sort in sort_keys else "name"
    direction = direction if direction in {"asc", "desc"} else "asc"
    items.sort(key=sort_keys[sort], reverse=direction == "desc")
    categories = [row[0] for row in db.session.query(PriceItem.category).filter(PriceItem.active.is_(True)).distinct().order_by(PriceItem.category).all()]
    suppliers = [row[0] for row in db.session.query(SupplierPartNumber.supplier).join(PriceItem).filter(PriceItem.active.is_(True)).distinct().order_by(SupplierPartNumber.supplier).all()]
    duplicate_pairs = find_near_duplicate_price_items(items) if review_duplicates else []
    active_sync = PriceSyncRun.query.filter(PriceSyncRun.status.in_(("queued", "running"))).order_by(PriceSyncRun.updated_at.desc()).first()
    latest_sync = PriceSyncRun.query.filter_by(status="complete").order_by(PriceSyncRun.updated_at.desc()).first()
    total_items = len(items)
    page_count = max(1, -(-total_items // PRICING_PAGE_SIZE))
    page = min(max(request.args.get("page", 1, type=int) or 1, 1), page_count)
    items = items[(page - 1) * PRICING_PAGE_SIZE: page * PRICING_PAGE_SIZE]
    page_args = {k: v for k, v in request.args.items() if k != "page"}
    last_catalog = PriceSyncRun.query.filter_by(mode="catalog").order_by(PriceSyncRun.id.desc()).first()
    return render_template("pricing.html", items=items, categories=categories, suppliers=suppliers, search=search, selected_category=category, selected_supplier=supplier, selected_unit=unit, selected_sort=sort, selected_direction=direction, review_duplicates=review_duplicates, duplicate_pairs=duplicate_pairs, labor_rate=get_labor_rate(), active_sync=active_sync, latest_sync=latest_sync,
                           total_items=total_items, page=page, page_count=page_count, page_args=page_args, unconfirmed_count=unconfirmed_count,
                           unconfirmed_label=UNCONFIRMED, caden_unconfirmed=caden_uses_unconfirmed(), catalog_batch=CATALOG_BATCH, catalog_batch_max=CATALOG_BATCH_MAX, last_catalog=last_catalog, catalog_done_count=len(catalog_done()))


PRICING_PAGE_SIZE = 250


@app.route("/quoting/labor-calculator")
@login_required
def labor_calculator():
    mike = AiAgent.query.filter(db.func.lower(AiAgent.name) == "mike").first()
    batches = LaborProposalBatch.query.order_by(LaborProposalBatch.created_at.desc()).limit(8).all()
    messages = MikeMessage.query.filter_by(user_id=current_user.id).order_by(MikeMessage.created_at.desc()).limit(30).all()[::-1]
    return render_template("labor_calculator.html", mike=mike, batches=batches, messages=messages, api_ready=bool(os.environ.get("OPENAI_API_KEY")), labor_rate=get_labor_rate())


@app.route("/quoting/labor-calculator/upload", methods=["POST"])
@login_required
def upload_labor_reference():
    check_csrf()
    mike = AiAgent.query.filter(db.func.lower(AiAgent.name) == "mike").first()
    upload = request.files.get("labor_reference")
    if mike is None or not upload or not upload.filename:
        flash("Choose a labor reference file.", "error")
        return redirect(url_for("labor_calculator"))
    extension = os.path.splitext(upload.filename)[1].lower()
    if extension not in {".pdf", ".txt", ".md", ".csv"}:
        flash("Labor references must be PDF, TXT, MD, or CSV.", "error")
        return redirect(url_for("labor_calculator"))
    upload_dir = os.path.join(basedir, "static", "uploads", "agents", str(mike.id))
    os.makedirs(upload_dir, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}-{secure_filename(upload.filename)}"
    path = os.path.join(upload_dir, stored_name); upload.save(path)
    try:
        extracted = extract_reference_text(path, extension)
    except Exception:
        app.logger.exception("Mike labor reference extraction failed"); extracted = ""
    reference = AgentReferenceFile(agent=mike, original_name=upload.filename, stored_name=stored_name, extracted_text=extracted)
    db.session.add(reference); db.session.flush()
    if not extracted:
        db.session.commit(); flash("The file was saved, but no readable text was found. Try a text-based PDF.", "error")
        return redirect(url_for("labor_calculator"))
    if not os.environ.get("OPENAI_API_KEY"):
        db.session.commit(); flash("The reference was indexed for Mike. Connect OpenAI before generating proposals.", "error")
        return redirect(url_for("labor_calculator"))
    try:
        batch = generate_labor_proposals(mike, reference)
        db.session.commit()
        flash(f"Mike prepared {len(batch.lines)} labor recommendation(s) for review." if batch.lines else "Mike indexed the file but found no supported Pricing Sheet labor changes.", "success")
    except Exception as exc:
        app.logger.exception("Labor proposal generation failed"); db.session.commit()
        flash(f"The file was indexed, but Mike could not build proposals. {describe_ai_error(exc)}", "error")
    return redirect(url_for("labor_calculator"))


@app.route("/quoting/labor-calculator/batches/<int:batch_id>/apply", methods=["POST"])
@login_required
def apply_labor_proposals(batch_id):
    check_csrf(); batch = db.get_or_404(LaborProposalBatch, batch_id)
    selected = {int(value) for value in request.form.getlist("proposal_id") if value.isdigit()}
    applied = 0
    for line in batch.lines:
        if line.id in selected and line.price_item and line.proposed_hours >= 0:
            line.price_item.labor_hours_per_unit = line.proposed_hours
            line.approved = True; applied += 1
    if applied:
        batch.status = "applied"; batch.approved_at = datetime.utcnow(); db.session.commit()
        flash(f"Applied {applied} approved labor-rate update(s) to the Pricing Sheet.", "success")
    else:
        flash("Select at least one recommendation to apply.", "error")
    return redirect(url_for("labor_calculator"))


@app.route("/quoting/labor-calculator/ask", methods=["POST"])
@login_required
def ask_mike():
    check_csrf(); question = request.form.get("question", "").strip()
    if not question:
        flash("Ask Mike a labor-time question.", "error"); return redirect(url_for("labor_calculator"))
    mike = AiAgent.query.filter(db.func.lower(AiAgent.name) == "mike", AiAgent.active.is_(True)).first()
    db.session.add(MikeMessage(user=current_user, role="user", content=question)); db.session.commit()
    if mike is None or not os.environ.get("OPENAI_API_KEY"):
        answer = "Mike is not available until the AI connection and agent are active. Your question was saved."
    else:
        try:
            answer = ask_mike_labor_question(mike, question)
        except Exception as exc:
            app.logger.exception("Mike labor question failed"); answer = f"I could not answer that yet. {describe_ai_error(exc)}"
    db.session.add(MikeMessage(user=current_user, role="assistant", content=answer)); db.session.commit()
    return redirect(url_for("labor_calculator"))


@app.route("/quoting/pricing/cooper-account", methods=["GET", "POST"])
@login_required
def cooper_account():
    credential = SupplierCredential.query.filter_by(supplier="cooper").first()
    if request.method == "POST":
        check_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username:
            flash("Cooper username is required.", "error")
        elif not password and credential is None:
            flash("Cooper password is required the first time.", "error")
        else:
            credential = credential or SupplierCredential(supplier="cooper")
            credential.username = username
            if password:
                credential.encrypted_password = encrypt_supplier_password(password)
            credential.last_test_ok = None; credential.last_error = ""
            db.session.add(credential); db.session.commit()
            flash("Cooper account saved securely. Test the connection before checking prices.", "success")
            return redirect(url_for("cooper_account"))
    return render_template("cooper_account.html", cooper=credential, cooper_username=credential.username if credential else "")


@app.route("/quoting/pricing/cooper-account/test", methods=["POST"])
@login_required
def test_cooper_account():
    check_csrf(); credential = SupplierCredential.query.filter_by(supplier="cooper").first()
    if credential is None:
        flash("Add a Cooper account first.", "error"); return redirect(url_for("cooper_account"))
    try:
        with CooperBrowserPricingClient(credential.username, decrypt_supplier_password(credential.encrypted_password)) as supplier:
            supplier.login()
        credential.last_test_ok = True; credential.last_error = ""; flash("Cooper connection successful. Account pricing is available.", "success")
    except Exception as exc:
        credential.last_test_ok = False; credential.last_error = supplier_error_message(exc); flash(f"Cooper connection failed: {credential.last_error}", "error")
    credential.last_tested_at = datetime.utcnow(); db.session.commit()
    return redirect(url_for("cooper_account"))


@app.route("/quoting/pricing/cooper-sync", methods=["POST"])
@login_required
def check_cooper_prices():
    check_csrf()
    only_zero_prices = request.form.get("scope") == "new"
    only_selected = request.form.get("scope") == "selected"
    selected_ids = sorted({int(v) for v in request.form.getlist("item_ids[]") + request.form.get("item_ids", "").split(",") if str(v).strip().isdigit()})
    expire_stale_cooper_runs()
    active = PriceSyncRun.query.filter(PriceSyncRun.status.in_(("queued", "running"))).order_by(PriceSyncRun.updated_at.desc()).first()
    if active:
        return jsonify({"error":"A Cooper price check is already queued or running.", "batch_id":active.batch_id, "status_url":url_for("cooper_sync_status", batch_id=active.batch_id), "preview_url":url_for("cooper_sync_preview", batch_id=active.batch_id)}), 409
    priced_query = PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.cooper_part_number != "")
    if only_zero_prices:
        priced_query = priced_query.filter(PriceItem.price <= 0)
    if only_selected:
        priced_query = priced_query.filter(PriceItem.id.in_(selected_ids or [0]))
    priced_items = priced_query.order_by(PriceItem.name).all()
    if not priced_items:
        message = ("None of the selected items has a Cooper part number." if only_selected else
                   "No $0 Pricing Sheet items have a Cooper part number." if only_zero_prices else "No Pricing Sheet items have a Cooper part number yet.")
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"error": message}), 400
        flash(message, "error"); return redirect(url_for("pricing_sheet"))
    batch = PriceSyncBatch(supplier="cooper", created_by=current_user)
    db.session.add(batch); db.session.flush()
    skipped = len(selected_ids) - len(priced_items) if only_selected else 0
    scope_label = ("selected items" + (f" ({skipped} without a Cooper part number left out)" if skipped else "")) if only_selected else "$0/new items only" if only_zero_prices else "all Cooper items"
    run = PriceSyncRun(batch=batch, total=len(priced_items), only_zero_prices=only_zero_prices,
                       item_ids_json=json.dumps([item.id for item in priced_items]) if only_selected else "",
                       activity_log=json.dumps([f"Price check queued for {len(priced_items)} {scope_label}. Waiting for the office listener."]))
    db.session.add(run); db.session.commit()
    return jsonify({"batch_id": batch.id, "status_url": url_for("cooper_sync_status", batch_id=batch.id), "preview_url": url_for("cooper_sync_preview", batch_id=batch.id)})


@app.route("/quoting/pricing/cooper-sync/<int:batch_id>/cancel", methods=["POST"])
@login_required
def cancel_cooper_price_sync(batch_id):
    check_csrf(); batch = db.get_or_404(PriceSyncBatch, batch_id)
    if batch.run and batch.run.status in ("queued", "running"):
        batch.run.status = "cancelled"
        append_sync_log(batch.run, "Price check cancelled from the Pricing Sheet.")
        db.session.commit()
        flash("Cooper price check cancelled.", "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/cooper-sync/<int:batch_id>/status")
@login_required
def cooper_sync_status(batch_id):
    batch = db.get_or_404(PriceSyncBatch, batch_id)
    run = batch.run
    if run is None:
        return jsonify({"status":"complete", "current":len(batch.rows), "total":len(batch.rows), "logs":["Legacy price check complete."], "preview_url":url_for("cooper_sync_preview", batch_id=batch.id)})
    try: logs = json.loads(run.activity_log or "[]")
    except (TypeError, json.JSONDecodeError): logs = []
    catalog = run.mode == "catalog"
    return jsonify({"status":run.status, "current":run.current, "total":run.total, "logs":logs[-35:], "error":run.error_message, "mode": run.mode,
                    "preview_url": url_for("pricing_sheet", category=UNCONFIRMED) if catalog else url_for("cooper_sync_preview", batch_id=batch.id),
                    "preview_label": "See the items added" if catalog else "Review proposed prices"})


@app.route("/quoting/pricing/cooper-sync/<int:batch_id>")
@login_required
def cooper_sync_preview(batch_id):
    batch = db.get_or_404(PriceSyncBatch, batch_id)
    if batch.run and batch.run.status in ("queued", "running"):
        return redirect(url_for("pricing_sheet", sync=batch.id))
    return render_template("cooper_sync_preview.html", batch=batch)


@app.route("/quoting/pricing/cooper-sync/<int:batch_id>/apply", methods=["POST"])
@login_required
def apply_cooper_prices(batch_id):
    check_csrf(); batch = db.get_or_404(PriceSyncBatch, batch_id)
    if batch.applied_at:
        flash("This Cooper price check was already applied.", "error"); return redirect(url_for("cooper_sync_preview", batch_id=batch.id))
    selected = {int(value) for value in request.form.getlist("row_id") if value.isdigit()}
    changed = 0
    for row in batch.rows:
        if row.id in selected and row.status == "matched" and row.new_price is not None:
            row.price_item.price = row.new_price; row.status = "applied"; changed += 1
    batch.applied_at = datetime.utcnow(); db.session.commit()
    flash(f"Applied {changed} Cooper price update(s). Linked services now use the new prices.", "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/labor-rate", methods=["POST"])
@login_required
def update_labor_rate():
    check_csrf()
    try:
        rate = max(float(request.form.get("labor_rate", "0")), 0)
    except ValueError:
        flash("Labor rate must be a number.", "error")
        return redirect(url_for("pricing_sheet"))
    set_company_setting("labor_rate", f"{rate:.2f}")
    flash(f"Labor rate updated to ${rate:,.2f} per hour.", "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/new", methods=["POST"])
@login_required
def create_price_item():
    check_csrf()
    error, item = save_price_item_from_form()
    if error:
        flash(error, "error")
    else:
        flash(f'Added "{item.name}" to the pricing sheet.', "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/<int:item_id>/edit", methods=["POST"])
@login_required
def edit_price_item(item_id):
    check_csrf()
    item = db.get_or_404(PriceItem, item_id)
    error, item = save_price_item_from_form(item)
    if request.headers.get("X-Requested-With") == "fetch":
        if error:
            db.session.rollback()
            item = db.session.get(PriceItem, item_id)
        return jsonify({"ok": not error, "error": error or "", "row": render_template("_price_row.html", item=item)}), (400 if error else 200)
    flash(error or f'Updated "{item.name}" everywhere it is used.', "error" if error else "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_price_item(item_id):
    check_csrf()
    item = db.get_or_404(PriceItem, item_id)
    name = item.name
    removed_lines = 0
    for link in list(item.service_links):
        if link.service_item:
            db.session.delete(link.service_item)
            removed_lines += 1
    item.active = False
    db.session.commit()
    flash(f'Removed "{name}" from the Pricing Sheet and {removed_lines} linked service material line(s). Confirmed quote history was preserved.', "success")
    return redirect(url_for("pricing_sheet"))


PRICE_IMAGE_DIR = os.path.join(basedir, "static", "uploads", "price-items")
PRICE_IMAGE_TYPES = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"), (b"GIF89a", "gif"))
PRICE_IMAGE_MAX = 3 * 1024 * 1024


def image_kind(data):
    """'jpg' · 'png' · 'gif' · 'webp' from the file's own bytes, or '' when it is not a picture we keep."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return next((kind for magic, kind in PRICE_IMAGE_TYPES if data.startswith(magic)), "")


def save_price_item_image(item, data, source):
    """Store a picture for a Pricing Sheet item. Returns an error sentence or ''."""
    kind = image_kind(data or b"")
    if not kind:
        return "That file is not a JPEG, PNG, GIF or WebP picture."
    if len(data) > PRICE_IMAGE_MAX:
        return "That picture is larger than 3 MB."
    os.makedirs(PRICE_IMAGE_DIR, exist_ok=True)
    name = "%d-%s.%s" % (item.id, hashlib.sha256(data).hexdigest()[:12], kind)
    with open(os.path.join(PRICE_IMAGE_DIR, name), "wb") as handle:
        handle.write(data)
    item.image_path = "uploads/price-items/" + name
    item.image_source = source
    return ""


@app.route("/quoting/pricing/<int:item_id>/image", methods=["POST"])
@login_required
def upload_price_item_image(item_id):
    check_csrf()
    item = db.get_or_404(PriceItem, item_id)
    upload = request.files.get("image")
    error = save_price_item_image(item, upload.read() if upload else b"", "upload") if upload else "Choose a picture."
    if not error:
        db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": not error, "error": error, "row": render_template("_price_row.html", item=item)}), (400 if error else 200)
    flash(error or f'Picture saved for "{item.name}".', "error" if error else "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/remove-selected", methods=["POST"])
@login_required
def remove_selected_price_items():
    check_csrf()
    ids = {int(v) for v in request.form.getlist("item_ids[]") + request.form.get("item_ids", "").split(",") if str(v).strip().isdigit()}
    removed, lines = [], 0
    for item in PriceItem.query.filter(PriceItem.id.in_(ids or [0]), PriceItem.active.is_(True)).all():
        for link in list(item.service_links):
            if link.service_item:
                db.session.delete(link.service_item)
                lines += 1
        item.active = False
        removed.append(item.name)
    db.session.commit()
    if removed:
        flash(f"Removed {len(removed)} item{'s' if len(removed) != 1 else ''} from the Pricing Sheet"
              + (f" and {lines} linked service material line{'s' if lines != 1 else ''}" if lines else "")
              + ". Confirmed quote history was preserved.", "success")
    else:
        flash("Select at least one item to remove.", "error")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/cleanup-duplicates", methods=["POST"])
@login_required
def cleanup_duplicate_price_items():
    check_csrf()
    groups = []
    for item in PriceItem.query.filter_by(active=True).order_by(PriceItem.id).all():
        item_parts = {(canonical_item_name(row.supplier), canonical_part_number(row.part_number)) for row in item.supplier_parts if canonical_part_number(row.part_number)}
        matching = next((group for group in groups if canonical_item_name(group[0].name) == canonical_item_name(item.name) or bool(item_parts & {(canonical_item_name(row.supplier), canonical_part_number(row.part_number)) for row in group[0].supplier_parts if canonical_part_number(row.part_number)})), None)
        if matching is None:
            groups.append([item])
        else:
            matching.append(item)
    merged = 0
    for group in groups:
        if len(group) < 2:
            continue
        keeper = max(group, key=lambda row: (len(row.service_links) + len(row.quote_lines), bool(row.labor_hours_per_unit), row.updated_at or datetime.min, -row.id))
        for duplicate in group:
            if duplicate.id == keeper.id:
                continue
            if not keeper.labor_hours_per_unit and duplicate.labor_hours_per_unit:
                keeper.labor_hours_per_unit = duplicate.labor_hours_per_unit
            for link in list(duplicate.service_links):
                line = link.service_item
                existing = next((candidate.service_item for candidate in keeper.service_links if candidate.service_item.service_id == line.service_id), None)
                if existing:
                    existing.quantity += line.quantity
                    if line.notes and line.notes not in existing.notes:
                        existing.notes = "; ".join(filter(None, [existing.notes, line.notes]))
                    db.session.delete(line)
                else:
                    link.price_item = keeper
                    line.name = keeper.name; line.unit = keeper.unit; line.unit_price = keeper.price
            for quote_line in list(duplicate.quote_lines):
                quote_line.price_item = keeper
            PriceSyncRow.query.filter_by(price_item_id=duplicate.id).update({"price_item_id": keeper.id})
            duplicate.active = False
            merged += 1
    db.session.commit()
    flash(f'Cleaned up {merged} duplicate Pricing Sheet item(s).' if merged else 'No exact duplicate names or matching supplier part numbers were found.', "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/services/new", methods=["GET", "POST"])
@login_required
def create_service():
    if request.method == "POST":
        check_csrf()
        service = Service()
        error = populate_service_from_form(service)
        if error:
            flash(error, "error")
            return render_template("service_form.html", service=None, form=request.form, pricing_items=PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all(), tool_catalog=ToolCatalog.query.filter_by(active=True).order_by(ToolCatalog.name).all(), return_quote_id=request.form.get("return_quote_id", type=int), return_missing_key=request.form.get("return_missing_key", "")), 400
        db.session.add(service)
        db.session.commit()
        flash(f'Created service "{service.name}".', "success")
        quote_id = request.form.get("return_quote_id", type=int)
        quote = db.session.get(Quote, quote_id) if quote_id else None
        if quote and quote.status == "draft":
            quote.messages.append(QuoteMessage(role="assistant", content=f'New service #{service.id} "{service.name}" is now available. Use it when repairing or reworking the quote.'))
            db.session.commit()
            flash("The new service is ready. Select it in Missing pricing and enter the quantity.", "success")
            return redirect(url_for("quote_assistant", job_id=quote.job_id, repair=request.form.get("return_missing_key", "")))
        return redirect(url_for("service_detail", service_id=service.id))
    form = {"name": request.args.get("name", ""), "category": "Quote repair", "example_quantity": "1 unit"}
    return render_template("service_form.html", service=None, form=form, pricing_items=PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all(), tool_catalog=ToolCatalog.query.filter_by(active=True).order_by(ToolCatalog.name).all(), return_quote_id=request.args.get("quote_id", type=int), return_missing_key=request.args.get("missing_key", ""))


@app.route("/quoting/services/<int:service_id>")
@login_required
def service_detail(service_id):
    return render_template("service_detail.html", service=db.get_or_404(Service, service_id))


SERVICE_CHECKLIST_CATEGORIES = [
    ("Wire & Cable", "≈", ("wire", "cable", "romex", "thhn", "thwn", "ser ", "seu ", "uf-b", "uf cable", "mc cable", "bx", "cat5", "cat6")),
    ("Conduit & Raceways", "∥", ("conduit", "emt", "raceway", "wiremold", "liquidtight", "liquid tight", "flex conduit", "rigid conduit", "pvc pipe")),
    ("Fittings & Connectors", "⊙", ("connector", "coupling", "bushing", "locknut", "lock nut", "adapter", "conduit body", "lb body", "strap", "clamp", "fitting")),
    ("Panels, Breakers & Disconnects", "▦", ("breaker", "panel", "load center", "loadcenter", "disconnect", "fuse", "fused")),
    ("Service Equipment", "ϟ", ("meter socket", "meter base", "weatherhead", "service head", "mast", "service entrance")),
    ("Lighting", "☼", ("light", "fixture", "wafer", "lamp", "bulb", "led", "ballast", "driver", "sconce", "chandelier")),
    ("Devices & Controls", "◉", ("receptacle", "outlet", "switch", "dimmer", "gfci", "afci", "occupancy", "sensor", "thermostat", "contactor", "relay")),
    ("Boxes, Covers & Plates", "□", ("junction box", "device box", "old work box", "new work box", "handy box", "pull box", "cover", "mud ring", "plaster ring", "wall plate", "faceplate")),
    ("Grounding & Bonding", "⌁", ("ground", "bond", "grounding", "ground rod", "acorn", "intersystem")),
    ("Fasteners & Consumables", "+", ("screw", "anchor", "bolt", "washer", "nut", "tape", "wire nut", "wirenut", "sealant", "caulk", "lubricant", "label", "zip tie", "cable tie")),
]


def build_service_order_checklist(service):
    grouped = {label: {"label": label, "icon": icon, "items": []} for label, icon, _ in SERVICE_CHECKLIST_CATEGORIES}
    grouped["Other Materials"] = {"label": "Other Materials", "icon": "◇", "items": []}
    for item in service.items:
        searchable = " ".join(filter(None, [item.current_name, item.notes, item.catalog_item.category if item.catalog_item else ""])).casefold()
        label = next((category for category, _, keywords in SERVICE_CHECKLIST_CATEGORIES if any(keyword in searchable for keyword in keywords)), "Other Materials")
        grouped[label]["items"].append(item)
    for group in grouped.values():
        group["items"].sort(key=lambda item: item.current_name.casefold())
    return [group for group in grouped.values() if group["items"]]


@app.route("/quoting/services/<int:service_id>/order-checklist")
@login_required
def service_order_checklist(service_id):
    service = db.get_or_404(Service, service_id)
    return render_template("service_order_checklist.html", service=service, checklist_groups=build_service_order_checklist(service))


@app.route("/quoting/services/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
def edit_service(service_id):
    service = db.get_or_404(Service, service_id)
    if request.method == "POST":
        check_csrf()
        error = populate_service_from_form(service)
        if error:
            flash(error, "error")
            return render_template("service_form.html", service=service, form=request.form, pricing_items=PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all(), tool_catalog=ToolCatalog.query.filter_by(active=True).order_by(ToolCatalog.name).all()), 400
        db.session.commit()
        flash(f'Updated service "{service.name}".', "success")
        return redirect(url_for("service_detail", service_id=service.id))
    return render_template("service_form.html", service=service, form={}, pricing_items=PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all(), tool_catalog=ToolCatalog.query.filter_by(active=True).order_by(ToolCatalog.name).all())


@app.route("/quoting/services/<int:service_id>/delete", methods=["POST"])
@login_required
def delete_service(service_id):
    check_csrf()
    service = db.get_or_404(Service, service_id)
    name = service.name
    QuoteOptionService.query.filter_by(service_id=service.id).update({"service_id": None})
    db.session.delete(service)
    db.session.commit()
    flash(f'Deleted service "{name}".', "success")
    return redirect(url_for("services"))


# ---------------- AI quote builder ----------------

@app.route("/quoting/generate")
@login_required
def generate_quote():
    search = request.args.get("q", "").strip()
    query = Job.query.join(Client).filter(~Job.status.in_(["completed", "declined"])).options(db.selectinload(Job.quotes), db.selectinload(Job.material_order))
    if search:
        like = f"%{search}%"
        terms = [Job.title.ilike(like), Job.description.ilike(like), Client.first_name.ilike(like), Client.last_name.ilike(like),
                 Client.company_name.ilike(like), Job.address_line1.ilike(like), Job.city.ilike(like)]
        if search.lstrip("#").isdigit():
            terms.append(Job.job_number == int(search.lstrip("#")))
        query = query.filter(db.or_(*terms))
    job_list = [job for job in query.order_by(Job.updated_at.desc()).all() if job.bucket == "in_works"]   # only jobs before a quote
    return render_template("quote_job_select.html", jobs=job_list, search=search, quick_quotes=QuickQuote.query.filter_by(assigned_job_id=None).order_by(QuickQuote.created_at.desc()).all())


@app.route("/quoting/quick", methods=["POST"])
@login_required
def create_quick_quote():
    check_csrf(); description = request.form.get("description", "").strip()
    if not description:
        flash("Describe the work before saving the quick quote.", "error"); return redirect(url_for("generate_quote"))
    option_count = request.form.get("option_count", type=int)
    option_count = option_count if option_count in {3, 6} else 6
    lead = QuickQuote(client_name=request.form.get("client_name", "").strip(), phone=request.form.get("phone", "").strip(), email=request.form.get("email", "").strip(), address=request.form.get("address", "").strip(), description=description, created_by=current_user, option_count=option_count)
    db.session.add(lead); db.session.flush()
    if os.environ.get("OPENAI_API_KEY"):
        try:
            shim_job = type("QuickJob", (), {"id": None, "title": f"Quick quote — {lead.client_name or 'unassigned'}", "description": lead.description, "job_address": lead.address})()
            shim_message = type("QuickMessage", (), {"role":"user", "content":lead.description})()
            shim_quote = type("QuickConversation", (), {"messages":[shim_message], "option_count": lead.option_count})()
            result = build_ai_quote_response(shim_job, shim_quote)
            lead.ai_result_json = json.dumps(result); lead.ai_message = result.get("message", "")
        except Exception:
            app.logger.exception("Quick quote generation failed"); lead.ai_message = "Saved successfully. Caden will generate the options after job assignment."
    else:
        lead.ai_message = "Saved successfully. Options will generate when the AI connection is available."
    db.session.commit(); flash("Quick quote saved and staged for job assignment.", "success")
    return redirect(url_for("generate_quote"))


@app.route("/quoting/quick/<int:quick_id>/assign", methods=["POST"])
@login_required
def assign_quick_quote(quick_id):
    check_csrf(); lead = db.get_or_404(QuickQuote, quick_id); job = db.session.get(Job, request.form.get("job_id", type=int))
    if not job: flash("Select a valid job.", "error"); return redirect(url_for("generate_quote"))
    lead.assigned_job = job
    if lead.description and lead.description not in job.description: job.description = (job.description + "\n\n" + lead.description).strip()
    draft = Quote.query.filter_by(job_id=job.id, status="draft", created_by_user_id=current_user.id).order_by(Quote.created_at.desc()).first()
    if draft is None:
        draft = Quote(job=job, created_by=current_user, revision=Quote.query.filter_by(job_id=job.id, status="confirmed").count()+1, title=f"{job.title} — Service Options", option_count=lead.option_count)
        db.session.add(draft)
    elif not draft.options:
        draft.option_count = lead.option_count
    draft.messages.append(QuoteMessage(role="user", content=lead.description))
    result = lead.ai_result
    if result.get("ready") and len(result.get("options", [])) == draft.option_count:
        notes, line_suggestions = apply_ai_quote_options(draft, result["options"]); remember_pricing_suggestions(draft, result.get("pricing_needed"), line_suggestions); draft.title = plain_terms(result.get("quote_title")) or draft.title; draft.introduction = plain_terms(result.get("introduction", ""))
        if not draft.ai_snapshot_json: draft.ai_snapshot_json = serialize_quote_snapshot(draft)
        draft.messages.append(QuoteMessage(role="assistant", content=lead.ai_message or f"I prepared {draft.option_count} options from the quick quote."))
    db.session.commit(); flash("Quick quote assigned to the job with its draft preserved.", "success")
    return redirect(url_for("quote_assistant", job_id=job.id))


def draft_quote_for(job):
    """This person's draft quote on the job, made (with Caden's greeting) when there is none."""
    quote = Quote.query.filter_by(job_id=job.id, status="draft", created_by_user_id=current_user.id).order_by(Quote.created_at.desc()).first()
    if quote is None:
        revision = Quote.query.filter_by(job_id=job.id, status="confirmed").count() + 1
        quote = Quote(job=job, created_by=current_user, revision=revision, title=f"{job.title} — Service Options")
        quote.messages.append(QuoteMessage(role="assistant", content="Tell me what the client wants done. I’ll make reasonable assumptions and build the selected options. I’ll only ask one short question when the answer would materially change the safety or scope."))
        db.session.add(quote)
        if job.status == "new_request":
            job.status = "working_quote"
        db.session.commit()

    return quote


def send_quote_message(job, quote, message):
    """One message from the office to Caden on a draft quote, and Caden's reply (options when ready). Never raises."""
    quote.messages.append(QuoteMessage(role="user", content=message))
    db.session.commit()
    if not os.environ.get("OPENAI_API_KEY"):
        quote.messages.append(QuoteMessage(role="assistant", content="The OpenAI API key has not been connected yet. Add it on PythonAnywhere, reload the app, and send this message again."))
        db.session.commit()
    else:
        try:
            result = build_ai_quote_response(job, quote)
            assistant_message = result.get("message", "I reviewed the request.").strip()
            questions = [q.strip() for q in result.get("questions", []) if q.strip()]
            if not result.get("ready"):
                if questions:
                    assistant_message += "\n\n" + "\n".join(f"{number}. {question}" for number, question in enumerate(questions, 1))
                else:
                    assistant_message = "Tell me a little more about the work, or say \"go ahead\" and I'll build the options with stated assumptions."
            quote.messages.append(QuoteMessage(role="assistant", content=assistant_message))
            if result.get("ready"):
                notes, line_suggestions = apply_ai_quote_options(quote, result.get("options", []))
                remember_pricing_suggestions(quote, result.get("pricing_needed"), line_suggestions)
                db.session.flush()
                if not quote.ai_snapshot_json:
                    quote.ai_snapshot_json = serialize_quote_snapshot(quote)
                summary = quote_ready_summary(quote, notes)
                if summary:
                    quote.messages.append(QuoteMessage(role="assistant", content=summary))
                quote.title = plain_terms(result.get("quote_title", "")) or f"{job.title} — Service Options"
                quote.introduction = plain_terms(result.get("introduction", ""))
            db.session.commit()
        except Exception as exc:
            app.logger.exception("Quote assistant failed")
            quote.messages.append(QuoteMessage(role="assistant", content=f"I couldn’t reach the AI service just now, so nothing changed. {describe_ai_error(exc)} Send your message again in a moment; your conversation and draft are saved."))
            db.session.commit()


@app.route("/quoting/generate/<int:job_id>", methods=["GET", "POST"])
@login_required
def quote_assistant(job_id):
    job = db.get_or_404(Job, job_id)
    quote = draft_quote_for(job)

    if quote.travel_destination_snapshot != job.job_address or not quote.travel_origin_snapshot:
        try:
            refresh_quote_travel(quote)
            db.session.commit()
        except Exception:
            db.session.commit()

    if request.method == "POST":
        check_csrf()
        try:
            refresh_quote_travel(quote)
            db.session.commit()
        except Exception as exc:
            db.session.commit()
            flash(f"Travel pricing could not be calculated: {' '.join(str(exc).split())}", "error")
            return redirect(url_for("quote_assistant", job_id=job.id))
        message = request.form.get("message", "").strip()
        if not message:
            flash("Enter a message for the quote assistant.", "error")
            return redirect(url_for("quote_assistant", job_id=job.id))
        send_quote_message(job, quote, message)
        return redirect(url_for("quote_assistant", job_id=job.id))

    return render_template("quote_assistant.html", job=job, quote=quote, api_ready=bool(os.environ.get("OPENAI_API_KEY")), missing_groups=build_missing_quote_groups(quote),
                           pricing_rows=pricing_needed_rows(quote) if quote.options else [])


@app.route("/quotes/<int:quote_id>/option-count", methods=["POST"])
@login_required
def set_quote_option_count(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "draft":
        flash("Confirmed quotes keep their original option count.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    option_count = request.form.get("option_count", type=int)
    if option_count not in {3, 6}:
        abort(400)
    if quote.option_count != option_count:
        quote.option_count = option_count
        quote.options.clear()
        quote.ai_snapshot_json = ""
        quote.messages.append(QuoteMessage(role="user", content=f"Build exactly {option_count} options from now on. The previous draft was intentionally cleared when I changed the option count."))
        db.session.commit()
        flash(f"Switched to {option_count} options. Send Caden the scope or ask it to rebuild.", "success")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quotes/<int:quote_id>/manual-outcome", methods=["POST"])
@login_required
def upload_manual_quote_outcome(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    upload = request.files.get("manual_quote")
    if not upload or not upload.filename:
        flash("Choose the manual quote file you actually used.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    extension = os.path.splitext(upload.filename)[1].lower()
    if extension not in {".pdf", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".webp"}:
        flash("Manual quotes must be PDF, TXT, MD, CSV, PNG, JPG, or WEBP.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    caden = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden").first()
    if caden is None:
        flash("Caden is not configured in Settings.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    upload_dir = os.path.join(basedir, "static", "uploads", "agents", str(caden.id))
    os.makedirs(upload_dir, exist_ok=True)
    stored_name = f"{secrets.token_hex(10)}-{secure_filename(upload.filename)}"
    path = os.path.join(upload_dir, stored_name); upload.save(path)
    extracted = ""
    if extension in {".pdf", ".txt", ".md", ".csv"}:
        try: extracted = extract_reference_text(path, extension)
        except Exception: app.logger.exception("Manual quote text extraction failed")
    notes = request.form.get("manual_quote_notes", "").strip()
    final_snapshot = serialize_quote_snapshot(quote)
    outcome = ManualQuoteUpload(quote=quote, uploaded_by=current_user, original_name=upload.filename, stored_name=stored_name, extracted_text=extracted, notes=notes, ai_snapshot_json=quote.ai_snapshot_json or "", final_snapshot_json=final_snapshot)
    db.session.add(outcome)
    reference_title = f"Manual quote outcome — Job #{quote.job.job_number} — {upload.filename}"
    db.session.add(AgentReferenceFile(agent=caden, original_name=reference_title, stored_name=stored_name, extracted_text=extracted))
    knowledge_content = f"The first Caden draft was not the final quote used for Job #{quote.job.job_number}. Administrator notes: {notes or 'No notes supplied.'}\n\nUse this outcome as correction evidence. Preserve documented manual decisions and identify recurring differences instead of repeating them."
    if extracted: knowledge_content += "\n\nMANUAL QUOTE TEXT:\n" + extracted[:20000]
    db.session.add(AgentKnowledge(agent=caden, title=f"Manual outcome — Job #{quote.job.job_number} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}", category="Quote outcomes", content=knowledge_content, active=True))
    db.session.commit()
    flash("Manual outcome saved. It is now visible in Caden's knowledge and retained with this quote for AI-vs-final comparison.", "success")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quotes/manual-outcomes/<int:outcome_id>/file")
@login_required
def manual_quote_file(outcome_id):
    outcome = db.get_or_404(ManualQuoteUpload, outcome_id)
    caden = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden").first_or_404()
    return send_from_directory(os.path.join(basedir, "static", "uploads", "agents", str(caden.id)), outcome.stored_name, as_attachment=request.args.get("download") == "1")


def build_missing_quote_groups(quote):
    grouped = {}
    services = Service.query.order_by(Service.name).all()
    price_items = PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all()
    for option in quote.options:
        for line in option.lines:
            if not line.is_missing:
                continue
            key = normalize_item_name(line.service_name) or f"line-{line.id}"
            matching_key = next((existing_key for existing_key in grouped if not existing_key.startswith("line-") and SequenceMatcher(None, existing_key, key).ratio() >= .88), key)
            group = grouped.setdefault(matching_key, {"key": matching_key, "name": line.service_name, "lines": [], "tiers": []})
            group["lines"].append(line)
            if option.tier not in group["tiers"]:
                group["tiers"].append(option.tier)
    for group in grouped.values():
        target = normalize_item_name(group["name"])
        service_scores = [(SequenceMatcher(None, target, normalize_item_name(item.name)).ratio(), item) for item in services]
        item_scores = [(SequenceMatcher(None, target, item.lookup_key).ratio(), item) for item in price_items]
        group["suggested_services"] = [item for score, item in sorted(service_scores, key=lambda pair: (-pair[0], pair[1].name))[:5] if score >= .22]
        group["suggested_items"] = [item for score, item in sorted(item_scores, key=lambda pair: (-pair[0], pair[1].name))[:5] if score >= .22]
        suggested_service_ids = {item.id for item in group["suggested_services"]}
        suggested_item_ids = {item.id for item in group["suggested_items"]}
        group["other_services"] = [item for item in services if item.id not in suggested_service_ids]
        group["other_items"] = [item for item in price_items if item.id not in suggested_item_ids]
    return list(grouped.values())


def build_ai_pricing_repair(quote, repair_instructions, only_missing_key=""):
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Connect the OpenAI API before using Caden's pricing repair.")
    missing_groups = build_missing_quote_groups(quote)
    if only_missing_key:
        missing_groups = [group for group in missing_groups if group["key"] == only_missing_key]
    if not missing_groups:
        raise ValueError("That missing pricing issue is no longer available.")
    services = [{"service_id": row.id, "name": row.name, "example_quantity": row.example_quantity, "material_price": round(row.material_total, 2), "labor_hours": round(row.estimated_labor_hours, 4), "installed_price": round(row.example_total, 2)} for row in Service.query.order_by(Service.name).all()]
    def _repair_row(row):
        return {"price_item_id": row.id, "name": row.name, "unit": row.unit, "material_unit_price": round(row.price, 2), "labor_hours_per_unit": round(row.labor_hours_per_unit, 4)}
    items = [_repair_row(row) for row in PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.category != UNCONFIRMED).order_by(PriceItem.name).all()]
    if caden_uses_unconfirmed():
        items += [dict(_repair_row(row), unconfirmed=True) for row in relevant_unconfirmed([repair_instructions] + [group["name"] for group in missing_groups], limit=40)]
    agent = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden", AiAgent.active.is_(True)).first()
    instructions = f"""You are Caden's structured pricing-repair tool.
{build_agent_context(agent)}

Translate the administrator's plain-language installation details into exact existing Service and Pricing Sheet IDs. Respect quantities and units exactly. A Service is a complete packaged scope; a Pricing Sheet item is a per-unit component with its own material price and saved labor hours per unit. If the administrator states labor time for an item, return a labor update for that exact item. If a selected item has zero labor hours and the editable agent knowledge explicitly supplies a labor estimate, save that estimate too. Otherwise leave labor at zero and explain what is still needed. Never invent an ID, price, quantity, or labor time. If an exact catalog match is unavailable, leave that repair unresolved and explain what Pricing Sheet item or Service must be created.

MISSING_GROUPS:
{json.dumps([{"missing_key": group["key"], "name": group["name"], "tiers": group["tiers"]} for group in missing_groups])}

SERVICE_LIBRARY:
{json.dumps(services)}

PRICE_LIBRARY:
{json.dumps(items)}
"""
    schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "labor_updates": {"type": "array", "items": {"type": "object", "properties": {"price_item_id": {"type": "integer"}, "hours_per_unit": {"type": "number", "minimum": 0}}, "required": ["price_item_id", "hours_per_unit"], "additionalProperties": False}},
            "repairs": {"type": "array", "items": {"type": "object", "properties": {
                "missing_key": {"type": "string"},
                "services": {"type": "array", "items": {"type": "object", "properties": {"service_id": {"type": "integer"}, "multiplier": {"type": "number", "exclusiveMinimum": 0}}, "required": ["service_id", "multiplier"], "additionalProperties": False}},
                "items": {"type": "array", "items": {"type": "object", "properties": {"price_item_id": {"type": "integer"}, "quantity": {"type": "number", "exclusiveMinimum": 0}}, "required": ["price_item_id", "quantity"], "additionalProperties": False}},
                "unresolved_reason": {"type": "string"},
            }, "required": ["missing_key", "services", "items", "unresolved_reason"], "additionalProperties": False}},
        },
        "required": ["message", "labor_updates", "repairs"],
        "additionalProperties": False,
    }
    response = OpenAI().responses.create(model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), instructions=instructions, input=repair_instructions, text={"format": {"type": "json_schema", "name": "powerhouse_pricing_repair", "schema": schema, "strict": True}})
    return json.loads(response.output_text)


def apply_ai_pricing_repair(quote, result):
    active_items = {row.id: row for row in PriceItem.query.filter_by(active=True).all()}
    services = {row.id: row for row in Service.query.all()}
    rate = get_labor_rate()
    labor_updates = 0
    for update in result.get("labor_updates", []):
        item = active_items.get(update.get("price_item_id"))
        hours = float(update.get("hours_per_unit", -1))
        if item is None or hours < 0:
            continue
        item.labor_hours_per_unit = hours
        labor_updates += 1
        for option in quote.options:
            for line in option.lines:
                if line.price_item_id == item.id:
                    line.labor_hours_per_unit = hours
                    line.labor_rate_snapshot = rate
                    line.material_unit_price = item.price
                    line.unit_price = item.price + (hours * rate)

    groups = {group["key"]: group for group in build_missing_quote_groups(quote)}
    repaired_groups = 0
    unresolved = []
    for repair in result.get("repairs", []):
        group = groups.get(repair.get("missing_key"))
        selected_services = [(services.get(row.get("service_id")), float(row.get("multiplier", 0))) for row in repair.get("services", [])]
        selected_items = [(active_items.get(row.get("price_item_id")), float(row.get("quantity", 0))) for row in repair.get("items", [])]
        selected_services = [(row, quantity) for row, quantity in selected_services if row is not None and quantity > 0]
        selected_items = [(row, quantity) for row, quantity in selected_items if row is not None and quantity > 0]
        if group is None or not (selected_services or selected_items):
            if repair.get("unresolved_reason"):
                unresolved.append(repair["unresolved_reason"].strip())
            continue
        lines_by_option = {}
        for line in group["lines"]:
            lines_by_option.setdefault(line.option, []).append(line)
        for option, old_lines in lines_by_option.items():
            first_position = min(line.position for line in old_lines)
            for line in old_lines:
                option.lines.remove(line)
            position = first_position
            for service, quantity in selected_services:
                existing = next((line for line in option.lines if line.service_id == service.id), None)
                if existing:
                    existing.multiplier += quantity
                else:
                    option.lines.append(QuoteOptionService(service=service, service_name=service.name, example_quantity=service.example_quantity, multiplier=quantity, unit_price=service.example_total, material_unit_price=service.material_total, labor_hours_per_unit=service.estimated_labor_hours, labor_rate_snapshot=rate, position=position))
                    position += 1
            for item, quantity in selected_items:
                existing = next((line for line in option.lines if line.price_item_id == item.id), None)
                if existing:
                    existing.multiplier += quantity
                else:
                    option.lines.append(QuoteOptionService(price_item=item, service_name=item.name, example_quantity=f"1 {item.unit}", multiplier=quantity, unit_price=item.price + (item.labor_hours_per_unit * rate), material_unit_price=item.price, labor_hours_per_unit=item.labor_hours_per_unit, labor_rate_snapshot=rate, position=position))
                    position += 1
        repaired_groups += 1
    return repaired_groups, labor_updates, unresolved


@app.route("/quotes/<int:quote_id>/ai-repair-pricing", methods=["POST"])
@login_required
def ai_repair_quote_pricing(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "draft":
        flash("Only a draft quote can be repaired.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    repair_instructions = request.form.get("repair_instructions", "").strip()
    missing_key = request.form.get("missing_key", "").strip()
    if not repair_instructions:
        flash("Tell Caden what parts, quantities, or labor time should be used.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    quote.messages.append(QuoteMessage(role="user", content=f"Pricing repair details: {repair_instructions}"))
    db.session.commit()
    try:
        result = build_ai_pricing_repair(quote, repair_instructions, missing_key)
        repaired, learned, unresolved = apply_ai_pricing_repair(quote, result)
        response_message = result.get("message", "Pricing details reviewed.").strip()
        if unresolved:
            response_message += " Still needed: " + " ".join(unresolved)
        quote.messages.append(QuoteMessage(role="assistant", content=response_message))
        db.session.commit()
        flash(f"Caden repaired {repaired} pricing issue(s) and saved {learned} labor estimate(s).", "success")
    except Exception:
        app.logger.exception("AI pricing repair failed")
        db.session.rollback()
        flash("Caden could not apply that pricing repair. Your existing quote was not changed.", "error")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quote-options/<int:option_id>/edit", methods=["POST"])
@login_required
def edit_quote_option(option_id):
    check_csrf()
    option = db.get_or_404(QuoteOption, option_id)
    quote = option.quote
    if quote.status != "draft":
        flash("Only draft options can be edited.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    title = request.form.get("title", "").strip()
    if not title:
        flash("The option title is required.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    option.title = title
    option.description = request.form.get("description", "").strip()
    option.bullets_json = json.dumps([line.strip(" •-\t") for line in request.form.get("bullets", "").splitlines() if line.strip(" •-\t")])
    option.manually_edited = True
    line_ids = request.form.getlist("line_id[]")
    names = request.form.getlist("line_name[]")
    quantities = request.form.getlist("line_quantity[]")
    materials = request.form.getlist("line_material[]")
    labor_hours = request.form.getlist("line_labor_hours[]")
    delete_line_ids = set(request.form.getlist("delete_line_ids"))
    option_lines = {str(line.id): line for line in option.lines}
    try:
        for index, line_id in enumerate(line_ids):
            line = option_lines.get(line_id)
            if line is None:
                continue
            if line_id in delete_line_ids:
                option.lines.remove(line)
                continue
            quantity = float(quantities[index]); material = float(materials[index]); hours = float(labor_hours[index])
            if quantity <= 0 or material < 0 or hours < 0:
                raise ValueError
            line.service_name = names[index].strip() or line.service_name
            line.multiplier = quantity
            line.material_unit_price = material
            line.labor_hours_per_unit = hours
            line.labor_rate_snapshot = get_labor_rate()
            line.unit_price = material + (hours * line.labor_rate_snapshot)
            line.manually_edited = True
            if line.service_id is None and line.price_item_id is None and (material > 0 or hours > 0):
                line.manual_priced = True
            if line.price_item and not line.price_item.labor_hours_per_unit and hours > 0:
                line.price_item.labor_hours_per_unit = hours
        new_name = request.form.get("new_line_name", "").strip()
        if new_name:
            new_quantity = float(request.form.get("new_line_quantity", "1")); new_material = float(request.form.get("new_line_material", "0")); new_labor = float(request.form.get("new_line_labor_hours", "0"))
            if new_quantity <= 0 or new_material < 0 or new_labor < 0:
                raise ValueError
            rate = get_labor_rate()
            option.lines.append(QuoteOptionService(service_name=new_name, example_quantity="Manual line", multiplier=new_quantity, material_unit_price=new_material, labor_hours_per_unit=new_labor, labor_rate_snapshot=rate, unit_price=new_material + (new_labor * rate), manual_priced=True, manually_edited=True, position=len(option.lines)))
    except (ValueError, IndexError):
        db.session.rollback()
        flash("Quantities must be greater than zero, and material prices and labor hours cannot be negative.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    quote.messages.append(QuoteMessage(role="user", content=f'Manual option edit: {option.tier} is now titled "{option.title}" with updated wording, quantities, material pricing, and labor. Preserve these decisions if the quote is reworked.'))
    db.session.commit()
    flash(f"Saved manual changes to the {option.tier} option.", "success")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quotes/<int:quote_id>/repair-missing", methods=["POST"])
@login_required
def repair_quote_missing(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "draft":
        flash("Only a draft quote can be repaired.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    missing_key = request.form.get("missing_key", "").strip()
    lines = []
    for option in quote.options:
        for line in option.lines:
            if not line.is_missing:
                continue
            line_key = normalize_item_name(line.service_name) or f"line-{line.id}"
            if line_key == missing_key or (not line_key.startswith("line-") and not missing_key.startswith("line-") and SequenceMatcher(None, line_key, missing_key).ratio() >= .88):
                lines.append(line)
    if not lines:
        flash("That missing line has already been fixed or is no longer available.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    strategy = request.form.get("strategy", "")
    try:
        quantity = float(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 0
    if quantity <= 0:
        flash("Quantity or multiplier must be greater than zero.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id, repair=missing_key))

    original_name = lines[0].service_name
    mapping_message = ""
    if strategy == "service":
        service = db.session.get(Service, request.form.get("service_id", type=int))
        if service is None:
            flash("Select an existing service.", "error")
            return redirect(url_for("quote_assistant", job_id=quote.job_id, repair=missing_key))
        for line in lines:
            line.service = service; line.price_item = None; line.service_name = service.name
            line.example_quantity = service.example_quantity; line.multiplier = quantity; line.unit_price = service.example_total
            line.material_unit_price = service.material_total; line.labor_hours_per_unit = service.estimated_labor_hours; line.labor_rate_snapshot = get_labor_rate()
        mapping_message = f'Pricing repair: "{original_name}" was mapped to service #{service.id} "{service.name}" at a {quantity:g}× multiplier. Respect this explicit mapping if the quote is reworked.'
        flash(f'Linked "{original_name}" to {quantity:g} × {service.name}.', "success")
    elif strategy in {"price_item", "new_item"}:
        if strategy == "price_item":
            price_item = db.session.get(PriceItem, request.form.get("price_item_id", type=int))
            if price_item is None:
                flash("Select a Pricing Sheet item.", "error")
                return redirect(url_for("quote_assistant", job_id=quote.job_id, repair=missing_key))
        else:
            item_name = request.form.get("new_item_name", "").strip() or original_name
            unit = request.form.get("new_item_unit", "ea").strip().casefold()
            if unit not in {"ea", "ft", "box", "roll", "lb", "job"}:
                unit = "ea"
            try:
                price = float(request.form.get("new_item_price", ""))
                labor_hours = float(request.form.get("new_item_labor_hours", "0") or 0)
            except ValueError:
                price = -1; labor_hours = -1
            if not item_name or price < 0 or labor_hours < 0:
                flash("Enter an item name, unit price, and non-negative labor time.", "error")
                return redirect(url_for("quote_assistant", job_id=quote.job_id, repair=missing_key))
            lookup_key = normalize_item_name(item_name)
            part_number = request.form.get("new_item_part_number", "").strip()
            price_item = find_duplicate_price_item(item_name, part_number, include_inactive=True)
            if price_item is None:
                price_item = PriceItem(name=item_name, lookup_key=lookup_key, category="Quote repair", unit=unit, price=price, labor_hours_per_unit=labor_hours, cooper_part_number=part_number, notes=f"Created while repairing quote for job #{quote.job.job_number}")
                db.session.add(price_item); db.session.flush()
            else:
                price_item.active = True; price_item.price = price; price_item.unit = unit; price_item.labor_hours_per_unit = labor_hours
        for line in lines:
            line.service = None; line.price_item = price_item; line.service_name = price_item.name
            line.example_quantity = f"1 {price_item.unit}"; line.multiplier = quantity
            line.material_unit_price = price_item.price; line.labor_hours_per_unit = price_item.labor_hours_per_unit; line.labor_rate_snapshot = get_labor_rate()
            line.unit_price = price_item.price + (price_item.labor_hours_per_unit * line.labor_rate_snapshot)
        mapping_message = f'Pricing repair: "{original_name}" was mapped to Pricing Sheet item #{price_item.id} "{price_item.name}" at quantity {quantity:g}. Respect this explicit mapping if the quote is reworked.'
        flash(f'Priced "{original_name}" as {quantity:g} × {price_item.name}.', "success")
    else:
        flash("Choose how to repair this missing line.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id, repair=missing_key))
    quote.messages.append(QuoteMessage(role="assistant", content=mapping_message))
    db.session.commit()
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quotes/<int:quote_id>/use-prices", methods=["POST"])
@login_required
def use_quote_prices(quote_id):
    """The "Needs a price" panel: price every listed item at once, in this quote (and in the Pricing Sheet when ticked)."""
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "draft":
        flash("Only a draft quote can be priced here.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    caden = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden").first()
    rate = get_labor_rate()
    priced, skipped = [], []
    removed = []
    for index in range(int(request.form.get("count", "0") or 0)):
        key = request.form.get(f"key_{index}", "").strip()
        price_text = request.form.get(f"price_{index}", "").strip()
        if key and request.form.get(f"skip_{index}"):
            for option in quote.options:
                for line in list(option.lines):
                    if line.is_missing and ((normalize_item_name(line.service_name) or f"line-{line.id}") == key or
                                            (not key.startswith("line-") and SequenceMatcher(None, normalize_item_name(line.service_name), key).ratio() >= .88)):
                        option.lines.remove(line)
                        db.session.delete(line)
            removed.append(request.form.get(f"name_{index}", "an item"))
            continue
        if not key or price_text == "":
            continue
        try:
            price = float(price_text.replace("$", "").replace(",", ""))
            labor = float((request.form.get(f"labor_{index}", "") or "0").strip())
        except ValueError:
            skipped.append(request.form.get(f"name_{index}", "an item")); continue
        if price < 0 or labor < 0:
            skipped.append(request.form.get(f"name_{index}", "an item")); continue
        unit = request.form.get(f"unit_{index}", "ea")
        unit = unit if unit in {"ea", "ft", "roll", "box", "lb", "job"} else "ea"
        lines = [line for option in quote.options for line in option.lines if line.is_missing and
                 ((normalize_item_name(line.service_name) or f"line-{line.id}") == key or
                  (not key.startswith("line-") and SequenceMatcher(None, normalize_item_name(line.service_name), key).ratio() >= .88))]
        if not lines:
            continue
        name = lines[0].service_name
        price_item = None
        if request.form.get(f"save_{index}"):
            price_item = find_duplicate_price_item(name, include_inactive=True)
            if price_item is None:
                price_item = PriceItem(name=name, lookup_key=normalize_item_name(name), category="Quote supplied", unit=unit, price=price,
                                       labor_hours_per_unit=labor, notes=f"Priced on the quote for job #{quote.job.job_number}.", active=True)
                db.session.add(price_item); db.session.flush()
            else:
                price_item.active = True; price_item.price = price; price_item.unit = unit; price_item.labor_hours_per_unit = labor
        for line in lines:
            line.service = None; line.price_item = price_item; line.manual_priced = price_item is None
            line.example_quantity = f"1 {unit}"; line.material_unit_price = price; line.labor_hours_per_unit = labor
            line.labor_rate_snapshot = rate; line.unit_price = price + labor * rate
        remember_direct_pricing(caden, name, unit, price, labor, f"Priced on the quote: {name} ${price:,.2f}/{unit}, {labor:g} labor hours.")
        priced.append(f"{name} at ${price:,.2f}/{unit}" + (f" + {labor:g} hr" if labor else "") + (" (saved to the Pricing Sheet)" if price_item else ""))
    if priced or removed:
        said = []
        if priced:
            said.append("Pricing confirmed on the quote: " + "; ".join(priced) + ".")
        if removed:
            said.append("Not needed (already covered), removed from the quote: " + "; ".join(removed) + ".")
        quote.messages.append(QuoteMessage(role="user", content=" ".join(said) + " Keep these decisions if the quote is reworked."))
    db.session.commit()
    if priced:
        flash("Priced " + ", ".join(priced) + ".", "success")
    if removed:
        flash("Removed as not needed: " + ", ".join(removed) + ".", "success")
    if skipped:
        flash("Check the price for " + ", ".join(skipped) + ": it must be a number.", "error")
    if not priced and not skipped and not removed:
        flash("Enter a price for at least one item, or tick Not needed.", "error")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


def capture_manual_quote_edits(quote):
    snapshots = {}
    for option in quote.options:
        if not option.manually_edited:
            continue
        snapshots[option.tier] = {
            "title": option.title,
            "description": option.description,
            "bullets_json": option.bullets_json,
            "lines": [{
                "service_id": line.service_id,
                "price_item_id": line.price_item_id,
                "service_name": line.service_name,
                "example_quantity": line.example_quantity,
                "multiplier": line.multiplier,
                "unit_price": line.unit_price,
                "material_unit_price": line.material_unit_price,
                "labor_hours_per_unit": line.labor_hours_per_unit,
                "labor_rate_snapshot": line.labor_rate_snapshot,
                "manual_priced": line.manual_priced,
                "position": line.position,
            } for line in option.lines if line.manually_edited],
        }
    return snapshots


def serialize_quote_snapshot(quote):
    return json.dumps({
        "option_count": quote.option_count,
        "title": quote.title,
        "introduction": quote.introduction,
        "travel_minutes": round(quote.travel_minutes_snapshot or 0, 2),
        "travel_cost": round(quote.travel_cost_snapshot or 0, 2),
        "travel_origin": quote.travel_origin_snapshot,
        "travel_destination": quote.travel_destination_snapshot,
        "travel_method": quote.travel_method_snapshot,
        "options": [{
            "tier": option.tier,
            "title": option.title,
            "description": option.description,
            "bullets": option.bullets,
            "material_total": round(option.material_total, 2),
            "labor_hours": round(option.labor_hours, 4),
            "labor_total": round(option.labor_total, 2),
            "total": round(option.total, 2),
            "lines": [{"name": line.service_name, "quantity": line.multiplier, "material_unit_price": line.material_unit_price, "labor_hours_per_unit": line.labor_hours_per_unit, "total": round(line.total, 2)} for line in option.lines],
        } for option in quote.options],
    })


def restore_manual_quote_edits(quote, snapshots):
    for option in quote.options:
        snapshot = snapshots.get(option.tier)
        if not snapshot:
            continue
        option.title = snapshot["title"]
        option.description = snapshot["description"]
        option.bullets_json = snapshot["bullets_json"]
        option.manually_edited = True
        for generated_line in list(option.lines):
            option.lines.remove(generated_line)
            db.session.delete(generated_line)
        for saved in snapshot["lines"]:
            line = QuoteOptionService(service_id=saved["service_id"], price_item_id=saved["price_item_id"], service_name=saved["service_name"], example_quantity=saved["example_quantity"], position=len(option.lines))
            option.lines.append(line)
            line.service_name = saved["service_name"]
            line.example_quantity = saved["example_quantity"]
            line.multiplier = saved["multiplier"]
            line.unit_price = saved["unit_price"]
            line.material_unit_price = saved["material_unit_price"]
            line.labor_hours_per_unit = saved["labor_hours_per_unit"]
            line.labor_rate_snapshot = saved["labor_rate_snapshot"]
            line.manual_priced = saved["manual_priced"] or (saved["service_id"] is None and saved["price_item_id"] is None)
            line.manually_edited = True


@app.route("/quotes/<int:quote_id>/rework", methods=["POST"])
@login_required
def rework_quote(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "draft":
        flash("Only a draft quote can be reworked.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    if not os.environ.get("OPENAI_API_KEY"):
        flash("Connect the OpenAI API before asking Caden to rework the quote.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    manual_snapshots = capture_manual_quote_edits(quote)
    quote.messages.append(QuoteMessage(role="user", content=f"Rework all {quote.option_count} options now using the repaired Service and Pricing Sheet mappings plus every manual option edit recorded in this conversation. Preserve administrator-edited scope, quantities, material prices, labor decisions, titles, descriptions, and bullets unless I explicitly asked to replace them. Improve organization and homeowner-facing wording. Ask only if one remaining fact materially changes the entire scope."))
    db.session.commit()
    try:
        result = build_ai_quote_response(quote.job, quote)
        quote.messages.append(QuoteMessage(role="assistant", content=result.get("message", f"I reworked all {quote.option_count} options using the repaired pricing context.")))
        if result.get("ready"):
            notes, line_suggestions = apply_ai_quote_options(quote, result.get("options", []))
            remember_pricing_suggestions(quote, result.get("pricing_needed"), line_suggestions)
            if not quote.ai_snapshot_json:
                quote.ai_snapshot_json = serialize_quote_snapshot(quote)
            restore_manual_quote_edits(quote, manual_snapshots)
            quote.title = plain_terms(result.get("quote_title", "")) or quote.title
            quote.introduction = plain_terms(result.get("introduction", ""))
            db.session.commit()
            flash(f"Caden reworked all {quote.option_count} options with the updated pricing context.", "success")
        else:
            questions = [item.strip() for item in result.get("questions", []) if item.strip()]
            if questions:
                quote.messages.append(QuoteMessage(role="assistant", content=questions[0]))
            db.session.commit()
            flash("Caden needs one answer before reworking the quote.", "error")
    except Exception as exc:
        app.logger.exception("Quote rework failed")
        db.session.rollback()
        flash(f"Caden could not rework the quote. {describe_ai_error(exc)} Your repaired pricing and existing draft remain saved.", "error")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quotes/<int:quote_id>/confirm", methods=["POST"])
@login_required
def confirm_quote(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "draft" or len(quote.options) != quote.option_count:
        flash(f"Generate all {quote.option_count} selected options before confirming the quote.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    if quote.has_missing_pricing:
        flash("Repair every missing price before confirming the quote.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    try:
        refresh_quote_travel(quote, force=True)
    except Exception as exc:
        db.session.commit()
        flash(f"The quote cannot be confirmed until travel pricing succeeds: {' '.join(str(exc).split())}", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    for option in quote.options:
        for line in option.lines:
            if line.manually_edited:
                continue
            if line.service:
                line.service_name = line.service.name
                line.example_quantity = line.service.example_quantity
                line.unit_price = line.service.example_total
                line.material_unit_price = line.service.material_total
                line.labor_hours_per_unit = line.service.estimated_labor_hours
                line.labor_rate_snapshot = get_labor_rate()
            elif line.price_item:
                line.service_name = line.price_item.name
                line.example_quantity = f"1 {line.price_item.unit}"
                line.material_unit_price = line.price_item.price
                line.labor_hours_per_unit = line.price_item.labor_hours_per_unit
                line.labor_rate_snapshot = get_labor_rate()
                line.unit_price = line.price_item.price + (line.price_item.labor_hours_per_unit * line.labor_rate_snapshot)
    quote.status = "confirmed"
    quote.confirmed_at = datetime.utcnow()
    if quote.job.status not in {"scheduled", "active", "completed"}:
        quote.job.status = "awaiting_decision"
    db.session.commit()
    flash(f"Quote version {quote.revision} confirmed and saved to the job.", "success")
    return redirect(url_for("quote_presentation", quote_id=quote.id))


@app.route("/quotes/<int:quote_id>/reopen", methods=["POST"])
@login_required
def reopen_quote(quote_id):
    check_csrf()
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "confirmed":
        flash("That quote is already open for editing.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    order = quote.job.material_order
    if order and order.quote_option and order.quote_option.quote_id == quote.id:
        flash("This version is tied to the customer's selected option and material order. Change the selected option before reopening it.", "error")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    other_drafts = Quote.query.filter(Quote.job_id == quote.job_id, Quote.status == "draft", Quote.id != quote.id).all()
    active_drafts = [draft for draft in other_drafts if draft.options or any(message.role == "user" for message in draft.messages)]
    if active_drafts:
        flash("This job already has a draft with work in it. Confirm that draft before reopening an older version.", "error")
        return redirect(url_for("job_detail", job_id=quote.job_id))
    for empty_draft in other_drafts:
        db.session.delete(empty_draft)
    quote.status = "draft"
    quote.confirmed_at = None
    quote.messages.append(QuoteMessage(role="assistant", content=f"Quote version {quote.revision} was reopened. Tell me what to change, edit any option directly, or use Rework quote with Caden."))
    if quote.job.status == "awaiting_decision":
        quote.job.status = "working_quote"
    db.session.commit()
    flash(f"Quote version {quote.revision} reopened for reworking. Its version number and existing options were preserved.", "success")
    return redirect(url_for("quote_assistant", job_id=quote.job_id))


@app.route("/quotes/<int:quote_id>")
@login_required
def quote_presentation(quote_id):
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "confirmed":
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    view = request.args.get("view", "with-pricing")
    if view not in {"with-pricing", "without-pricing"}:
        view = "with-pricing"
    return render_template("quote_presentation.html", quote=quote, show_pricing=view == "with-pricing", view=view)


@app.route("/quotes/<int:quote_id>/manual-edit", methods=["GET", "POST"])
@login_required
def edit_saved_quote(quote_id):
    quote = db.get_or_404(Quote, quote_id)
    if quote.status != "confirmed":
        flash("Use the live quote editor while this quote is still a draft.", "error")
        return redirect(url_for("quote_assistant", job_id=quote.job_id))
    if request.method == "POST":
        check_csrf()
        title = request.form.get("quote_title", "").strip()
        if not title:
            flash("The quote title is required.", "error")
            return redirect(url_for("edit_saved_quote", quote_id=quote.id))
        quote.title = title
        quote.introduction = request.form.get("quote_introduction", "").strip()
        try:
            for option in quote.options:
                option_title = request.form.get(f"option_title_{option.id}", "").strip()
                if not option_title:
                    raise ValueError(f"The {option.tier} option title is required.")
                final_price = float(request.form.get(f"option_price_{option.id}", ""))
                if final_price < 0:
                    raise ValueError("Option prices cannot be negative.")
                option.title = option_title
                option.description = request.form.get(f"option_description_{option.id}", "").strip()
                bullet_values = request.form.getlist(f"option_bullets_{option.id}[]")
                bullets = [line for line in (professionalize_scope_bullet(value) for value in bullet_values) if line]
                option.bullets_json = json.dumps(bullets)
                option.manual_total_override = final_price
                option.manually_edited = True
        except (TypeError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc) or "Enter a valid non-negative final price for every option.", "error")
            return redirect(url_for("edit_saved_quote", quote_id=quote.id))
        db.session.commit()
        flash(f"Saved manual wording and pricing for quote version {quote.revision}.", "success")
        return redirect(url_for("quote_presentation", quote_id=quote.id))
    return render_template("quote_manual_edit.html", quote=quote)


# ---------------- Sales ----------------

@app.route("/sales/onsite-checklist")
@login_required
def onsite_checklist():
    jobs = [job for job in Job.query.filter(~Job.status.in_(["completed", "declined"])).options(db.selectinload(Job.quotes), db.selectinload(Job.material_order)).order_by(Job.updated_at.desc()).all()
            if job.bucket == "in_works"]
    return render_template("onsite_checklist_select.html", jobs=jobs)


@app.route("/sales/onsite-checklist/<int:job_id>")
@login_required
def onsite_checklist_job(job_id):
    job = db.get_or_404(Job, job_id)
    inspection = OnsiteInspection.query.filter_by(job_id=job.id).order_by(OnsiteInspection.updated_at.desc()).first()
    return render_template("onsite_checklist.html", job=job, inspection=inspection, checklist=ONSITE_CHECKLIST, findings=inspection.findings if inspection else {}, top_sellers=TOP_SELLER_OPPORTUNITIES, api_ready=bool(os.environ.get("OPENAI_API_KEY")))


@app.route("/sales/onsite-checklist/<int:job_id>/save", methods=["POST"])
@login_required
def save_onsite_checklist(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    inspection = OnsiteInspection.query.filter_by(job_id=job.id).order_by(OnsiteInspection.updated_at.desc()).first()
    if inspection is None:
        inspection = OnsiteInspection(job=job, created_by=current_user)
        db.session.add(inspection)
    allowed = {f"{category}::{position}": item for category, items in ONSITE_CHECKLIST for position, item in enumerate(items)}
    selected = set(request.form.getlist("finding")) & set(allowed)
    findings = {category: [item for position, item in enumerate(items) if f"{category}::{position}" in selected] for category, items in ONSITE_CHECKLIST}
    inspection.findings_json = json.dumps({key: values for key, values in findings.items() if values})
    inspection.observations = request.form.get("observations", "").strip()
    inspection.client_priorities = request.form.get("client_priorities", "").strip()
    home_age = request.form.get("home_age", "unknown")
    inspection.home_age = home_age if home_age in {"older", "newer", "unknown"} else "unknown"
    selected_top_sellers = [item for item in request.form.getlist("top_seller") if item in TOP_SELLER_OPPORTUNITIES]
    inspection.top_sellers_json = json.dumps(selected_top_sellers)
    upload_dir = os.path.join(app.static_folder, "uploads", "inspections")
    os.makedirs(upload_dir, exist_ok=True)
    for upload in request.files.getlist("photos"):
        if not upload or not upload.filename:
            continue
        extension = os.path.splitext(upload.filename)[1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"}:
            flash(f'Skipped "{upload.filename}" because it is not a supported image.', "error")
            continue
        stored_name = f"{secrets.token_hex(10)}-{secure_filename(upload.filename)}"
        upload.save(os.path.join(upload_dir, stored_name))
        inspection.photos.append(OnsiteInspectionPhoto(original_name=upload.filename, stored_name=stored_name))
    inspection.updated_at = datetime.utcnow()
    if not inspection.ai_tip:
        inspection.ai_tip = generate_inspection_tip(job)
    db.session.commit()
    flash(f"Saved {inspection.checked_count} onsite finding(s) to job #{job.job_number}.", "success")
    return redirect(url_for("onsite_checklist_job", job_id=job.id))


@app.route("/sales/inspection-photos/<path:filename>")
@login_required
def inspection_photo(filename):
    return send_from_directory(os.path.join(app.static_folder, "uploads", "inspections"), secure_filename(filename))


@app.route("/sales/inspection-photos/<int:photo_id>/delete", methods=["POST"])
@login_required
def delete_inspection_photo(photo_id):
    check_csrf()
    photo = db.get_or_404(OnsiteInspectionPhoto, photo_id)
    job_id = photo.inspection.job_id
    db.session.delete(photo); db.session.commit()
    flash("Inspection photo removed.", "success")
    return redirect(url_for("onsite_checklist_job", job_id=job_id))


@app.route("/sales/onsite-checklist/<int:job_id>/tip", methods=["POST"])
@login_required
def refresh_inspection_tip(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    inspection = OnsiteInspection.query.filter_by(job_id=job.id).order_by(OnsiteInspection.updated_at.desc()).first()
    if inspection is None:
        inspection = OnsiteInspection(job=job, created_by=current_user)
        db.session.add(inspection)
    inspection.ai_tip = generate_inspection_tip(job)
    db.session.commit()
    flash("Powerhouse tip refreshed from the job description.", "success")
    return redirect(url_for("onsite_checklist_job", job_id=job.id))


@app.route("/sales/reviews")
@login_required
def sales_reviews():
    setting = db.session.get(CompanySetting, "google_review_url")
    review_url = setting.value.strip() if setting and setting.value.strip() else "https://search.google.com/local/writereview?placeid=ChIJuTfwafODKGURv8sw6Pg4T1c&source=g.page.m.kd._&laa=lu-desktop-review-solicitation&pli=1"
    return render_template("sales_reviews.html", review_url=review_url)


# ---------------- Clients, jobs, and schedule ----------------

@app.route("/clients")
@login_required
def clients():
    search = request.args.get("q", "").strip()
    query = Client.query
    if search:
        query = query.filter(db.or_(Client.first_name.ilike(f"%{search}%"), Client.last_name.ilike(f"%{search}%"), Client.email.ilike(f"%{search}%"), Client.phone.ilike(f"%{search}%"), Client.address_line1.ilike(f"%{search}%"), Client.company_name.ilike(f"%{search}%"), Client.tags.ilike(f"%{search}%")))
    sort = request.args.get("sort", "last")
    orders = {"last": (db.func.lower(Client.last_name), db.func.lower(Client.first_name)),
              "last_desc": (db.func.lower(Client.last_name).desc(), db.func.lower(Client.first_name).desc()),
              "first": (db.func.lower(Client.first_name), db.func.lower(Client.last_name)),
              "first_desc": (db.func.lower(Client.first_name).desc(), db.func.lower(Client.last_name).desc()),
              "newest": (Client.created_at.desc(),)}
    sort = sort if sort in orders else "last"
    return render_template("clients.html", clients=query.order_by(*orders[sort]).all(), search=search, sort=sort)


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def create_client():
    if request.method == "POST":
        check_csrf()
        client = Client()
        error = populate_client_from_form(client)
        if error:
            flash(error, "error")
            return render_template("client_form.html", client=None, form=request.form), 400
        db.session.add(client)
        db.session.commit()
        flash(f'Added client "{client.full_name}".', "success")
        next_page = request.args.get("next")
        return redirect(url_for("create_job", client_id=client.id) if next_page == "job" else url_for("client_detail", client_id=client.id))
    return render_template("client_form.html", client=None, form={})


@app.route("/clients/<int:client_id>")
@login_required
def client_detail(client_id):
    return render_template("client_detail.html", client=db.get_or_404(Client, client_id))


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = db.get_or_404(Client, client_id)
    if request.method == "POST":
        check_csrf()
        error = populate_client_from_form(client)
        if error:
            flash(error, "error")
            return render_template("client_form.html", client=client, form=request.form), 400
        db.session.commit()
        flash(f'Updated "{client.full_name}".', "success")
        return redirect(url_for("client_detail", client_id=client.id))
    return render_template("client_form.html", client=client, form={})


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    check_csrf()
    client = db.get_or_404(Client, client_id)
    if client.jobs:
        flash(f'"{client.full_name}" has job records and cannot be deleted.', "error")
    else:
        name = client.full_name
        db.session.delete(client)
        db.session.commit()
        flash(f'Deleted client "{name}".', "success")
    return redirect(url_for("clients"))


@app.route("/jobs")
@login_required
def jobs():
    tab = request.args.get("tab", "active")
    if tab not in {"in_works", "quoted", "active", "completed", "all"}:
        tab = "active"
    search = request.args.get("q", "").strip()
    query = Job.query.join(Client).options(db.selectinload(Job.quotes), db.selectinload(Job.material_order))
    if search:
        query = query.filter(db.or_(Job.title.ilike(f"%{search}%"), Job.description.ilike(f"%{search}%"), Client.first_name.ilike(f"%{search}%"), Client.last_name.ilike(f"%{search}%"), Job.address_line1.ilike(f"%{search}%")))
    all_jobs = query.order_by(Job.updated_at.desc()).all()
    shown_jobs = all_jobs if tab == "all" else [job for job in all_jobs if job.bucket == tab]
    counts = {key: sum(job.bucket == key for job in all_jobs) for key in ("in_works", "quoted", "active", "completed")}
    counts["all"] = len(all_jobs)
    return render_template("jobs.html", jobs=shown_jobs, tab=tab, counts=counts, search=search)


@app.route("/jobs/new", methods=["GET", "POST"])
@login_required
def create_job():
    client_list = Client.query.order_by(Client.last_name, Client.first_name).all()
    if not client_list:
        flash("Add a client before creating a job.", "error")
        return redirect(url_for("create_client", next="job"))
    if request.method == "POST":
        check_csrf()
        job = Job()
        error = populate_job_from_form(job)
        if error:
            flash(error, "error")
            return render_template("job_form.html", job=None, form=request.form, clients=client_list, users=User.query.order_by(User.username).all(), statuses=JOB_STATUSES), 400
        job.job_number = next_job_number()
        db.session.add(job)
        db.session.commit()
        flash(f'Created job "{job.title}".', "success")
        return redirect(url_for("job_detail", job_id=job.id))
    selected_client_id = request.args.get("client_id", type=int)
    return render_template("job_form.html", job=None, form={}, clients=client_list, users=User.query.order_by(User.username).all(), statuses=JOB_STATUSES, selected_client_id=selected_client_id)


@app.route("/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    return render_template("job_detail.html", job=db.get_or_404(Job, job_id))


def preferred_supplier_snapshot(price_item):
    if not price_item or not price_item.supplier_parts:
        return "", ""
    supplier_part = next((row for row in price_item.supplier_parts if "cooper" in canonical_item_name(row.supplier)), price_item.supplier_parts[0])
    return supplier_part.supplier, supplier_part.part_number


def build_material_order_from_option(order, option):
    """Copy an accepted quote option into a job-owned, editable material list."""
    order.items.clear()
    grouped = {}

    def add_item(price_item, name, quantity, unit, unit_price, supplier, part_number, source):
        if quantity <= 0:
            return
        key = ("catalog", price_item.id) if price_item else ("snapshot", canonical_item_name(name), unit.casefold(), canonical_part_number(part_number))
        existing = grouped.get(key)
        if existing:
            existing.quantity += quantity
            sources = [value.strip() for value in existing.source_services.split(";") if value.strip()]
            if source and source not in sources:
                sources.append(source)
                existing.source_services = "; ".join(sources)
            return
        row = JobMaterialOrderItem(
            price_item=price_item,
            name=name,
            quantity=quantity,
            unit=unit or "ea",
            unit_price=max(unit_price or 0, 0),
            supplier=supplier,
            part_number=part_number,
            source_services=source,
            position=len(grouped),
        )
        order.items.append(row)
        grouped[key] = row

    for quote_line in option.lines:
        multiplier = max(quote_line.multiplier or 0, 0)
        if quote_line.service:
            for service_item in quote_line.service.items:
                catalog = service_item.catalog_item
                supplier, part_number = preferred_supplier_snapshot(catalog)
                add_item(
                    catalog,
                    service_item.current_name,
                    (service_item.quantity or 0) * multiplier,
                    service_item.current_unit,
                    service_item.current_unit_price,
                    supplier,
                    part_number,
                    quote_line.service_name,
                )
        elif quote_line.price_item:
            supplier, part_number = preferred_supplier_snapshot(quote_line.price_item)
            add_item(
                quote_line.price_item,
                quote_line.price_item.name,
                multiplier,
                quote_line.price_item.unit,
                quote_line.material_unit_price if quote_line.material_unit_price is not None else quote_line.price_item.price,
                supplier,
                part_number,
                "Individual quote item",
            )
        elif quote_line.manual_priced and (quote_line.material_unit_price or 0) > 0:
            add_item(None, quote_line.service_name, multiplier, "ea", quote_line.material_unit_price, "", "", "Manual quote line")


@app.route("/jobs/<int:job_id>/accepted-option", methods=["POST"])
@login_required
def select_job_quote_option(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    option = db.session.get(QuoteOption, request.form.get("option_id", type=int))
    if not option or option.quote.job_id != job.id or option.quote.status != "confirmed":
        flash("Select an option from a confirmed quote for this job.", "error")
        return redirect(url_for("job_detail", job_id=job.id))
    order = job.material_order or JobMaterialOrder(job=job)
    db.session.add(order)
    order.quote_option = option
    order.selected_by = current_user
    order.selected_at = datetime.utcnow()
    order.status = "draft"
    order.cart_ready_at = None
    build_material_order_from_option(order, option)
    if job.status not in {"approved", "scheduled", "active", "completed"}:
        job.status = "approved"          # the client picked an option: the quote is approved
    db.session.commit()
    flash(f'{option.tier} selected. The job material order is ready to review.', "success")
    return redirect(url_for("job_material_order", job_id=job.id))


@app.route("/jobs/<int:job_id>/materials")
@login_required
def job_material_order(job_id):
    job = db.get_or_404(Job, job_id)
    if not job.material_order:
        flash("Choose the customer’s accepted quote option first.", "error")
        return redirect(url_for("job_detail", job_id=job.id))
    return render_template("job_material_order.html", job=job, order=job.material_order, pricing_items=PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all())


@app.route("/jobs/<int:job_id>/materials/update", methods=["POST"])
@login_required
def update_job_material_order(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    order = job.material_order
    if not order:
        abort(404)
    item_ids = request.form.getlist("item_id[]")
    names = request.form.getlist("item_name[]")
    quantities = request.form.getlist("item_quantity[]")
    units = request.form.getlist("item_unit[]")
    prices = request.form.getlist("item_unit_price[]")
    suppliers = request.form.getlist("item_supplier[]")
    part_numbers = request.form.getlist("item_part_number[]")
    notes = request.form.getlist("item_notes[]")
    remove_ids = {value for value in request.form.getlist("remove_item_id[]")}
    rows = {str(item.id): item for item in order.items}
    try:
        for index, item_id in enumerate(item_ids):
            item = rows.get(item_id)
            if not item:
                continue
            if item_id in remove_ids:
                order.items.remove(item)
                continue
            name = names[index].strip()
            quantity = float(quantities[index])
            unit_price = float(prices[index])
            if not name or quantity <= 0 or unit_price < 0:
                raise ValueError
            item.name = name
            item.quantity = quantity
            item.unit = units[index].strip() or "ea"
            item.unit_price = unit_price
            item.supplier = suppliers[index].strip()
            item.part_number = part_numbers[index].strip()
            item.notes = notes[index].strip()
    except (ValueError, IndexError):
        db.session.rollback()
        flash("Every kept material needs a name, a quantity greater than zero, and a non-negative price.", "error")
        return redirect(url_for("job_material_order", job_id=job.id))
    for position, item in enumerate(order.items):
        item.position = position
    order.status = "draft"
    order.cart_ready_at = None
    db.session.commit()
    flash("Material order changes saved.", "success")
    return redirect(url_for("job_material_order", job_id=job.id))


@app.route("/jobs/<int:job_id>/materials/add", methods=["POST"])
@login_required
def add_job_material_order_item(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    order = job.material_order
    if not order:
        abort(404)
    name = request.form.get("name", "").strip()
    try:
        quantity = float(request.form.get("quantity", "1"))
        unit_price = float(request.form.get("unit_price", "0"))
    except ValueError:
        quantity = 0
        unit_price = -1
    if not name or quantity <= 0 or unit_price < 0:
        flash("Enter an item name, quantity greater than zero, and a non-negative price.", "error")
        return redirect(url_for("job_material_order", job_id=job.id))
    price_item_id = request.form.get("price_item_id", type=int)
    price_item = db.session.get(PriceItem, price_item_id) if price_item_id else None
    if price_item and not price_item.active:
        price_item = None
    supplier = request.form.get("supplier", "").strip()
    part_number = request.form.get("part_number", "").strip()
    if price_item:
        name = price_item.name
        unit = price_item.unit
        unit_price = price_item.price
        supplier, part_number = preferred_supplier_snapshot(price_item)
    else:
        unit = request.form.get("unit", "ea").strip() or "ea"
    order.items.append(JobMaterialOrderItem(
        price_item=price_item,
        name=name,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        supplier=supplier,
        part_number=part_number,
        source_services="Added to job order",
        notes=request.form.get("notes", "").strip(),
        position=len(order.items),
    ))
    order.status = "draft"
    order.cart_ready_at = None
    db.session.commit()
    flash(f'Added "{name}" to the job order.', "success")
    return redirect(url_for("job_material_order", job_id=job.id))


@app.route("/jobs/<int:job_id>/materials/add-to-cart", methods=["POST"])
@login_required
def add_job_material_order_to_cart(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    order = job.material_order
    if not order or not order.items:
        flash("Add at least one material before preparing the cart.", "error")
        return redirect(url_for("job_material_order", job_id=job.id))
    order.status = "cart_ready"
    order.cart_ready_at = datetime.utcnow()
    db.session.commit()
    flash("Cart marked ready. Your external ordering program can be connected to this job order next.", "success")
    return redirect(url_for("job_material_order", job_id=job.id))


@app.route("/finance")
@login_required
def finance_dashboard():
    period = request.args.get("period", "month")
    today = date.today()
    if period == "week": start = today - timedelta(days=6)
    elif period == "year": start = today - timedelta(days=364)
    elif period == "all": start = date(2000, 1, 1)
    else: period = "month"; start = today - timedelta(days=29)
    jobs = Job.query.filter(Job.status == "completed", Job.updated_at >= datetime.combine(start, datetime.min.time())).order_by(Job.updated_at.desc()).all()
    revenue = sum(job.contract_amount or 0 for job in jobs)
    materials = sum(job.total_material_cost for job in jobs)
    labor = sum(job.labor_cost for job in jobs)
    profit = revenue - materials - labor
    return render_template("finance.html", jobs=jobs, period=period, revenue=revenue, materials=materials, labor=labor, profit=profit)


@app.route("/finance/expenses")
@login_required
def expenses():
    query = Expense.query
    if not current_user.can("view_finance"): query = query.filter_by(submitted_by_user_id=current_user.id)
    return render_template("expenses.html", expenses=query.order_by(Expense.expense_date.desc(), Expense.created_at.desc()).all(), categories=EXPENSE_CATEGORIES, jobs=Job.query.order_by(Job.updated_at.desc()).all(), today=date.today().isoformat())


@app.route("/finance/expenses/new", methods=["POST"])
@login_required
def create_expense():
    check_csrf(); vendor = request.form.get("vendor", "").strip(); category = request.form.get("category", "")
    if not vendor or category not in EXPENSE_CATEGORIES:
        flash("Vendor and a valid expense category are required.", "error"); return redirect(url_for("expenses"))
    try: total = max(float(request.form.get("total", "0")), 0); expense_date = date.fromisoformat(request.form.get("expense_date", date.today().isoformat()))
    except ValueError: flash("Enter a valid total and date.", "error"); return redirect(url_for("expenses"))
    job = db.session.get(Job, request.form.get("job_id", type=int))
    expense = Expense(submitted_by=current_user, job=job, vendor=vendor, total=total, category=category, reimbursable=request.form.get("reimbursable") == "on", notes=request.form.get("notes", "").strip(), expense_date=expense_date)
    upload = request.files.get("receipt")
    if upload and upload.filename:
        extension = os.path.splitext(upload.filename)[1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}: flash("Receipt must be an image or PDF.", "error"); return redirect(url_for("expenses"))
        upload_dir = os.path.join(app.static_folder, "uploads", "receipts"); os.makedirs(upload_dir, exist_ok=True)
        expense.receipt_filename = f"{secrets.token_hex(8)}-{secure_filename(upload.filename)}"; upload.save(os.path.join(upload_dir, expense.receipt_filename))
    db.session.add(expense); db.session.commit(); flash("Expense submitted.", "success")
    return redirect(url_for("expenses"))


@app.route("/finance/receipts/<path:filename>")
@login_required
def expense_receipt(filename):
    return send_from_directory(os.path.join(app.static_folder, "uploads", "receipts"), secure_filename(filename))


@app.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@login_required
def edit_job(job_id):
    job = db.get_or_404(Job, job_id)
    client_list = Client.query.order_by(Client.last_name, Client.first_name).all()
    if request.method == "POST":
        check_csrf()
        error = populate_job_from_form(job)
        if error:
            flash(error, "error")
            return render_template("job_form.html", job=job, form=request.form, clients=client_list, users=User.query.order_by(User.username).all(), statuses=JOB_STATUSES), 400
        db.session.commit()
        flash(f'Updated job "{job.title}".', "success")
        return redirect(url_for("job_detail", job_id=job.id))
    return render_template("job_form.html", job=job, form={}, clients=client_list, users=User.query.order_by(User.username).all(), statuses=JOB_STATUSES)


@app.route("/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id):
    check_csrf()
    job = db.get_or_404(Job, job_id)
    title = job.title
    visit_count = JobVisit.query.filter_by(job_id=job.id).count()
    JobVisit.query.filter_by(job_id=job.id).delete(synchronize_session=False)
    Expense.query.filter_by(job_id=job.id).update({"job_id": None})
    QuickQuote.query.filter_by(assigned_job_id=job.id).update({"assigned_job_id": None})
    db.session.delete(job)
    db.session.commit()
    flash(f'Deleted job "{title}" and {visit_count} attached job visit(s). Payroll clock sessions and detached expenses were preserved.', "success")
    return redirect(url_for("jobs"))


@app.route("/schedule")
@login_required
def schedule():
    today = date.today()
    try:
        anchor = date.fromisoformat(request.args.get("date", today.isoformat()))
    except ValueError:
        anchor = today
    view = request.args.get("view", "month")
    if view not in {"month", "week", "day"}: view = "month"
    first_day = anchor.replace(day=1)
    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(anchor.year, anchor.month)
    week_start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    week_days = [week_start + timedelta(days=i) for i in range(7)]
    events = {}
    job_query = Job.query
    if not current_user.can("view_all_schedules"):
        job_query = job_query.filter_by(assigned_user_id=current_user.id)
    for job in job_query.all():
        if job.review_at:
            events.setdefault(job.review_at.date(), []).append({"job": job, "type": "review", "label": "Site review", "time": job.review_at.strftime("%-I:%M %p")})
        if job.quote_presentation_at:
            events.setdefault(job.quote_presentation_at.date(), []).append({"job": job, "type": "quote", "label": "Read quote", "time": job.quote_presentation_at.strftime("%-I:%M %p")})
        if job.work_start_date:
            for offset in range(max(job.duration_days or 1, 1)):
                work_date = job.work_start_date + timedelta(days=offset)
                events.setdefault(work_date, []).append({"job": job, "type": "work", "label": f"Work day {offset + 1}/{max(job.duration_days or 1, 1)}", "time": ""})
    jump = 1 if view == "day" else 7 if view == "week" else 31
    previous_date = (first_day - timedelta(days=1)) if view == "month" else anchor - timedelta(days=jump)
    next_date = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1) if view == "month" else anchor + timedelta(days=jump)
    return render_template("schedule.html", weeks=weeks, week_days=week_days, events=events, current_month=first_day, anchor=anchor, previous_date=previous_date, next_date=next_date, today=today, view=view)


# ---------------- Time tracking ----------------

@app.route("/time-tracking")
@login_required
def time_tracking():
    active_clock = ClockSession.query.filter_by(user_id=current_user.id, clock_out=None).order_by(ClockSession.clock_in.desc()).first()
    active_visit = None
    if active_clock:
        active_visit = JobVisit.query.filter_by(clock_session_id=active_clock.id, ended_at=None).first()
    today = date.today()
    scheduled_jobs = []
    for job in Job.query.filter(~Job.status.in_(["completed", "declined"])).order_by(Job.title).all():
        work_today = job.work_start_date and job.work_start_date <= today < job.work_start_date + timedelta(days=max(job.duration_days or 1, 1))
        if work_today or (job.review_at and job.review_at.date() == today) or (job.quote_presentation_at and job.quote_presentation_at.date() == today):
            scheduled_jobs.append(job)
    recent = ClockSession.query.filter_by(user_id=current_user.id).order_by(ClockSession.clock_in.desc()).limit(30).all()
    return render_template("time_tracking.html", active_clock=active_clock, active_visit=active_visit, scheduled_jobs=scheduled_jobs, jobs=Job.query.filter(~Job.status.in_(["completed", "declined"])).order_by(Job.title).all(), sessions=recent, now=datetime.utcnow())


@app.route("/time-tracking/clock-in", methods=["POST"])
@login_required
def clock_in():
    check_csrf()
    if ClockSession.query.filter_by(user_id=current_user.id, clock_out=None).first():
        flash("You are already clocked in.", "error")
    else:
        db.session.add(ClockSession(user=current_user, notes=request.form.get("notes", "").strip()))
        db.session.commit()
        flash("Clocked in.", "success")
    return redirect(url_for("time_tracking"))


@app.route("/time-tracking/clock-out", methods=["POST"])
@login_required
def clock_out():
    check_csrf()
    clock = ClockSession.query.filter_by(user_id=current_user.id, clock_out=None).first()
    if not clock:
        flash("You are not clocked in.", "error")
    else:
        now = datetime.utcnow()
        for visit in clock.visits:
            if visit.ended_at is None:
                visit.ended_at = now
        clock.clock_out = now
        db.session.commit()
        flash("Clocked out. Any active job visit was stopped too.", "success")
    return redirect(url_for("time_tracking"))


@app.route("/time-tracking/start-visit", methods=["POST"])
@login_required
def start_job_visit():
    check_csrf()
    clock = ClockSession.query.filter_by(user_id=current_user.id, clock_out=None).first()
    if not clock:
        flash("Clock in before starting a job visit.", "error")
    elif JobVisit.query.filter_by(clock_session_id=clock.id, ended_at=None).first():
        flash("Stop the current job visit before starting another.", "error")
    else:
        job = db.session.get(Job, request.form.get("job_id", type=int))
        if not job:
            flash("Select a valid job.", "error")
        else:
            db.session.add(JobVisit(clock_session=clock, job=job, notes=request.form.get("notes", "").strip(), hourly_rate=current_user.hourly_rate or 0))
            db.session.commit()
            flash(f'Started visit for "{job.title}".', "success")
    return redirect(url_for("time_tracking"))


@app.route("/time-tracking/stop-visit", methods=["POST"])
@login_required
def stop_job_visit():
    check_csrf()
    clock = ClockSession.query.filter_by(user_id=current_user.id, clock_out=None).first()
    visit = JobVisit.query.filter_by(clock_session_id=clock.id, ended_at=None).first() if clock else None
    if not visit:
        flash("No job visit is running.", "error")
    else:
        visit.ended_at = datetime.utcnow()
        db.session.commit()
        flash("Job visit stopped. General paid time continues until you clock out.", "success")
    return redirect(url_for("time_tracking"))


@app.route("/time-tracking/timesheet")
@login_required
def timesheet():
    period_value = request.args.get("period", "")
    try:
        start = date.fromisoformat(period_value)
    except ValueError:
        anchor_start = date(2026, 8, 13)
        cycles = (date.today() - anchor_start).days // 14
        start = anchor_start + timedelta(days=cycles * 14)
    end = start + timedelta(days=14)
    selected_user_id = (request.args.get("user_id", type=int) or current_user.id) if current_user.can("view_all_timesheets") else current_user.id
    sessions = ClockSession.query.filter(ClockSession.user_id == selected_user_id, ClockSession.clock_in >= datetime.combine(start, datetime.min.time()), ClockSession.clock_in < datetime.combine(end, datetime.min.time())).order_by(ClockSession.clock_in).all()
    users = User.query.order_by(User.username).all() if current_user.can("view_all_timesheets") else [current_user]
    return render_template("timesheet.html", sessions=sessions, start=start, end=end, period_end=end-timedelta(days=1), previous=start-timedelta(days=14), next_period=start+timedelta(days=14), selected_user_id=selected_user_id, users=users)


# ---------------- Bookkeeping tasks ----------------

@app.route("/bookkeeping/tasks")
@login_required
def tasks():
    task_list = WorkTask.query.order_by(WorkTask.due_date.is_(None), WorkTask.due_date, WorkTask.created_at.desc()).all()
    columns = {status: [task for task in task_list if task.status == status] for status in TASK_STATUSES}
    messages = TaskAgentMessage.query.filter_by(user_id=current_user.id).order_by(TaskAgentMessage.created_at.desc()).limit(30).all()[::-1]
    return render_template("tasks.html", columns=columns, status_labels=TASK_STATUSES, users=User.query.order_by(User.username).all(), messages=messages, api_ready=bool(os.environ.get("OPENAI_API_KEY")))


@app.route("/bookkeeping/tasks/brendon", methods=["POST"])
@login_required
def ask_brendon():
    check_csrf()
    message = request.form.get("message", "").strip()
    if not message:
        flash("Tell Brendon what needs done or what was completed.", "error")
        return redirect(url_for("tasks"))
    db.session.add(TaskAgentMessage(user=current_user, role="user", content=message)); db.session.commit()
    try:
        result = build_brendon_response(message)
        action_results = apply_brendon_actions(result.get("actions", []))
        response_text = result.get("message", "").strip() or "I reviewed the task list."
        if action_results:
            response_text += "\n\n" + "\n".join(f"• {item}" for item in action_results)
    except Exception:
        app.logger.exception("Brendon task assistant failed")
        response_text = "I could not update tasking just now. Check the API key and error log, then try again."
    db.session.add(TaskAgentMessage(user=current_user, role="assistant", content=response_text)); db.session.commit()
    return redirect(url_for("tasks"))


@app.route("/bookkeeping/tasks/new", methods=["POST"])
@login_required
def create_task():
    check_csrf()
    title = request.form.get("title", "").strip()
    if not title:
        flash("Task title is required.", "error")
        return redirect(url_for("tasks"))
    task = WorkTask(title=title, description=request.form.get("description", "").strip(), category=request.form.get("category", "").strip() or "General", priority=request.form.get("priority", "normal"), created_by=current_user)
    assigned_id = request.form.get("assigned_user_id", "")
    task.assigned_user = db.session.get(User, int(assigned_id)) if assigned_id.isdigit() else None
    due = request.form.get("due_date", "")
    task.due_date = date.fromisoformat(due) if due else None
    db.session.add(task); db.session.commit()
    flash("Task created.", "success")
    return redirect(url_for("tasks"))


@app.route("/bookkeeping/tasks/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    check_csrf()
    task = db.get_or_404(WorkTask, task_id)
    status = request.form.get("status", "")
    if status in TASK_STATUSES:
        task.status = status
        task.completed_at = datetime.utcnow() if status == "completed" else None
        db.session.commit()
    return redirect(url_for("tasks"))


@app.route("/bookkeeping/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    check_csrf(); db.session.delete(db.get_or_404(WorkTask, task_id)); db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("tasks"))


# ---------------- Training and Darwin ----------------

@app.route("/bookkeeping/training")
@login_required
def trainings():
    return render_template("trainings.html", trainings=Training.query.order_by(Training.training_date.desc(), Training.created_at.desc()).all())


@app.route("/bookkeeping/training/new", methods=["GET", "POST"])
@login_required
def create_training():
    if request.method == "POST":
        check_csrf()
        title = request.form.get("title", "").strip()
        if not title:
            flash("Training title is required.", "error")
            return render_template("training_form.html", training=None, form=request.form), 400
        training = Training(title=title, provider=request.form.get("provider", "").strip(), category=request.form.get("category", "").strip() or "General", attendees=request.form.get("attendees", "").strip(), description=request.form.get("description", "").strip(), notes=request.form.get("notes", "").strip(), created_by=current_user)
        date_value = request.form.get("training_date", "")
        training.training_date = date.fromisoformat(date_value) if date_value else None
        db.session.add(training); db.session.commit()
        flash("Training record created.", "success")
        return redirect(url_for("training_detail", training_id=training.id))
    return render_template("training_form.html", training=None, form={})


@app.route("/bookkeeping/training/<int:training_id>")
@login_required
def training_detail(training_id):
    return render_template("training_detail.html", training=db.get_or_404(Training, training_id), api_ready=bool(os.environ.get("OPENAI_API_KEY")))


@app.route("/bookkeeping/training/<int:training_id>/edit", methods=["GET", "POST"])
@login_required
def edit_training(training_id):
    training = db.get_or_404(Training, training_id)
    if request.method == "POST":
        check_csrf()
        title = request.form.get("title", "").strip()
        if not title:
            flash("Training title is required.", "error")
            return render_template("training_form.html", training=training, form=request.form), 400
        training.title = title
        training.provider = request.form.get("provider", "").strip()
        training.category = request.form.get("category", "").strip() or "General"
        training.attendees = request.form.get("attendees", "").strip()
        training.description = request.form.get("description", "").strip()
        training.notes = request.form.get("notes", "").strip()
        date_value = request.form.get("training_date", "")
        try: training.training_date = date.fromisoformat(date_value) if date_value else None
        except ValueError:
            flash("Enter a valid training date.", "error")
            return render_template("training_form.html", training=training, form=request.form), 400
        db.session.commit(); flash("Training updated.", "success")
        return redirect(url_for("training_detail", training_id=training.id))
    return render_template("training_form.html", training=training, form={})


@app.route("/bookkeeping/training/<int:training_id>/files", methods=["POST"])
@login_required
def upload_training_files(training_id):
    check_csrf(); training = db.get_or_404(Training, training_id)
    uploads = [file for file in request.files.getlist("files") if file and file.filename]
    upload_dir = os.path.join(basedir, "static", "uploads", "training", str(training.id)); os.makedirs(upload_dir, exist_ok=True)
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt", ".md", ".csv"}
    added = 0
    for upload in uploads:
        extension = os.path.splitext(upload.filename)[1].lower()
        if extension not in allowed: continue
        stored = f"{secrets.token_hex(8)}-{secure_filename(upload.filename)}"; path = os.path.join(upload_dir, stored); upload.save(path)
        extracted = ""
        if extension in {".pdf", ".txt", ".md", ".csv"}:
            try: extracted = extract_reference_text(path, extension)
            except Exception: app.logger.exception("Training file extraction failed")
        db.session.add(TrainingFile(training=training, original_name=upload.filename, stored_name=stored, mime_type=upload.mimetype or "", extracted_text=extracted)); added += 1
    db.session.commit(); flash(f"Uploaded {added} training file(s).", "success")
    return redirect(url_for("training_detail", training_id=training.id))


@app.route("/bookkeeping/training-files/<int:file_id>")
@login_required
def training_file(file_id):
    reference = db.get_or_404(TrainingFile, file_id)
    directory = os.path.join(basedir, "static", "uploads", "training", str(reference.training_id))
    return send_from_directory(directory, reference.stored_name, as_attachment=request.args.get("download") == "1")


@app.route("/bookkeeping/training/<int:training_id>/summarize", methods=["POST"])
@login_required
def summarize_training(training_id):
    check_csrf(); training = db.get_or_404(Training, training_id)
    try:
        training.ai_summary = ask_darwin(training, "Create a concise training summary with key lessons, safety points, and practical field takeaways.")
        db.session.commit(); flash("Darwin updated the training summary.", "success")
    except Exception:
        app.logger.exception("Darwin summary failed"); flash("Darwin could not create the summary. Check the API key and error log.", "error")
    return redirect(url_for("training_detail", training_id=training.id))


@app.route("/bookkeeping/training/<int:training_id>/ask", methods=["POST"])
@login_required
def ask_training(training_id):
    check_csrf(); training = db.get_or_404(Training, training_id); question = request.form.get("question", "").strip()
    if question:
        training.messages.append(TrainingMessage(role="user", content=question))
        try: training.messages.append(TrainingMessage(role="assistant", content=ask_darwin(training, question)))
        except Exception: app.logger.exception("Darwin Q&A failed"); training.messages.append(TrainingMessage(role="assistant", content="I could not answer just now. Check the API key and error log, then try again."))
        db.session.commit()
    return redirect(url_for("training_detail", training_id=training.id))


@app.route("/settings", methods=["GET"])
@login_required
def settings():
    return render_settings_page()


@app.route("/settings/routing", methods=["POST"])
@login_required
def update_routing_settings():
    check_csrf(); address = request.form.get("dispatch_address", "").strip()
    if not address:
        flash("A dispatch address is required.", "error")
    else:
        try:
            road_factor = float(request.form.get("travel_road_factor", "1.25"))
            average_speed = float(request.form.get("travel_average_speed", "45"))
            if not 1 <= road_factor <= 3 or not 10 <= average_speed <= 80:
                raise ValueError
        except ValueError:
            flash("Road factor must be 1–3 and average speed must be 10–80 mph.", "error")
            return redirect(url_for("settings"))
        set_company_setting("dispatch_address", address)
        set_company_setting("travel_road_factor", road_factor)
        set_company_setting("travel_average_speed", average_speed)
        Job.query.update({"drive_minutes":None, "drive_distance_miles":None, "drive_checked_at":None, "drive_error":""})
        db.session.commit()
        flash("Free travel-estimation settings saved. Automatic job estimates will be recalculated for future quotes.", "success")
    return redirect(url_for("settings"))


TOOL_TYPES = ["Hand Tool", "Power Tool", "Test Equipment", "Access Equipment", "Safety Equipment", "Vehicle / Trailer", "Rental", "Other"]


@app.route("/settings/tools")
@login_required
def tools_library():
    return render_template("tools_library.html", tools=ToolCatalog.query.filter_by(active=True).order_by(ToolCatalog.tool_type, ToolCatalog.name).all(), tool_types=TOOL_TYPES)


def populate_tool_from_form(tool):
    name = request.form.get("name", "").strip()
    tool_type = request.form.get("tool_type", "Other").strip()
    if not name:
        return "Tool name is required."
    duplicate = ToolCatalog.query.filter(ToolCatalog.lookup_key == normalize_item_name(name), ToolCatalog.id != tool.id).first()
    if duplicate and duplicate.active:
        return f'A tool named "{duplicate.name}" already exists.'
    try:
        rental_cost = max(float(request.form.get("rental_cost", "0") or 0), 0)
    except ValueError:
        return "Rental cost must be a number."
    tool.name = name
    tool.lookup_key = normalize_item_name(name)
    tool.tool_type = tool_type if tool_type in TOOL_TYPES else "Other"
    tool.rental_cost = rental_cost if tool.tool_type == "Rental" else 0
    tool.notes = request.form.get("notes", "").strip()
    tool.active = True
    return None


@app.route("/settings/tools/new", methods=["POST"])
@login_required
def create_tool():
    check_csrf()
    requested_name = request.form.get("name", "").strip()
    tool = ToolCatalog.query.filter_by(lookup_key=normalize_item_name(requested_name), active=False).first() or ToolCatalog()
    error = populate_tool_from_form(tool)
    if error:
        flash(error, "error")
    else:
        db.session.add(tool)
        db.session.commit()
        flash(f'Added "{tool.name}" to the Tools library.', "success")
    return redirect(url_for("tools_library"))


@app.route("/settings/tools/<int:tool_id>/edit", methods=["POST"])
@login_required
def edit_tool(tool_id):
    check_csrf()
    tool = db.get_or_404(ToolCatalog, tool_id)
    error = populate_tool_from_form(tool)
    if error:
        db.session.rollback()
        flash(error, "error")
    else:
        db.session.commit()
        flash(f'Updated "{tool.name}" everywhere it is used.', "success")
    return redirect(url_for("tools_library"))


@app.route("/settings/tools/<int:tool_id>/delete", methods=["POST"])
@login_required
def delete_tool(tool_id):
    check_csrf()
    tool = db.get_or_404(ToolCatalog, tool_id)
    tool.active = False
    db.session.commit()
    flash(f'Removed "{tool.name}" from the available Tools list. Existing Services keep their reference.', "success")
    return redirect(url_for("tools_library"))


@app.route("/settings/bug-reports")
@login_required
def settings_bug_reports():
    selected_status = request.args.get("status", "active")
    query = BugReport.query
    if selected_status == "active":
        query = query.filter(BugReport.status.in_(["open", "in_progress"]))
    elif selected_status in BUG_STATUSES:
        query = query.filter_by(status=selected_status)
    reports = query.order_by(
        db.case((BugReport.priority == "critical", 0), (BugReport.priority == "high", 1), (BugReport.priority == "normal", 2), else_=3),
        BugReport.created_at.desc(),
    ).all()
    counts = {status: BugReport.query.filter_by(status=status).count() for status in BUG_STATUSES}
    return render_template("bug_reports.html", reports=reports, selected_status=selected_status, counts=counts, bug_statuses=BUG_STATUSES, bug_priorities=BUG_PRIORITIES)


@app.route("/settings/bug-reports/new", methods=["POST"])
@login_required
def create_bug_report():
    check_csrf()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not title or not description:
        flash("A bug title and description are required.", "error")
        return redirect(url_for("settings_bug_reports"))
    priority = request.form.get("priority", "normal")
    report = BugReport(
        title=title,
        description=description,
        area=request.form.get("area", "").strip() or "General",
        page_url=request.form.get("page_url", "").strip(),
        priority=priority if priority in BUG_PRIORITIES else "normal",
        created_by=current_user,
    )
    db.session.add(report)
    db.session.commit()
    flash(f'Bug #{report.id} reported.', "success")
    return redirect(url_for("settings_bug_reports", status="active") + f"#bug-{report.id}")


@app.route("/settings/bug-reports/<int:bug_id>", methods=["POST"])
@login_required
def update_bug_report(bug_id):
    check_csrf()
    report = db.get_or_404(BugReport, bug_id)
    status = request.form.get("status", report.status)
    priority = request.form.get("priority", report.priority)
    if status not in BUG_STATUSES or priority not in BUG_PRIORITIES:
        abort(400)
    report.status = status
    report.priority = priority
    report.resolution_notes = request.form.get("resolution_notes", "").strip()
    report.completed_at = datetime.utcnow() if status == "completed" else None
    db.session.commit()
    flash(f'Bug #{report.id} marked {BUG_STATUSES[status].lower()}.', "success")
    return redirect(url_for("settings_bug_reports", status=request.form.get("return_status", "active")) + f"#bug-{report.id}")


def render_settings_page(new_local_cooper_token=None):
    users = User.query.order_by(User.username).all()
    agents = AiAgent.query.order_by(AiAgent.name).all()
    token_setting = db.session.get(CompanySetting, LOCAL_COOPER_TOKEN_KEY)
    return render_template(
        "settings.html",
        users=users,
        agents=agents,
        roles=Role.query.order_by(Role.name).all(),
        local_cooper_token_active=token_setting is not None,
        new_local_cooper_token=new_local_cooper_token,
        jobber_connected=jobber_connected(), jobber_state=jobber_pull_state(), jobber_last_import=jobber_json("last_import"),
        listener_latest=listener_latest_version(), listener_running=(db.session.get(CompanySetting, "cooper_listener_version").value if db.session.get(CompanySetting, "cooper_listener_version") else ""),
        dispatch_address=get_dispatch_address(),
        travel_road_factor=get_numeric_setting("travel_road_factor", 1.25),
        travel_average_speed=get_numeric_setting("travel_average_speed", 45),
    )


@app.route("/settings/local-cooper-token/generate", methods=["POST"])
@login_required
def generate_local_cooper_token():
    check_csrf()
    token = secrets.token_urlsafe(36)
    setting = db.session.get(CompanySetting, LOCAL_COOPER_TOKEN_KEY) or CompanySetting(key=LOCAL_COOPER_TOKEN_KEY)
    setting.value = generate_password_hash(token)
    db.session.add(setting)
    db.session.commit()
    flash("Local Cooper Updater token generated. Copy it now; it will not be shown again.", "success")
    return render_settings_page(new_local_cooper_token=token)


@app.route("/settings/local-cooper-token/revoke", methods=["POST"])
@login_required
def revoke_local_cooper_token():
    check_csrf()
    setting = db.session.get(CompanySetting, LOCAL_COOPER_TOKEN_KEY)
    if setting:
        db.session.delete(setting)
        db.session.commit()
    flash("Local Cooper Updater access revoked.", "success")
    return redirect(url_for("settings"))


@app.route("/api/local-cooper/items", methods=["GET"])
@local_cooper_api_required
def local_cooper_items():
    items = PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.cooper_part_number != "").order_by(PriceItem.name).all()
    return jsonify({
        "items": [{
            "id": item.id,
            "name": item.name,
            "part_number": item.cooper_part_number.strip(),
            "unit": item.unit,
            "current_price": item.price,
        } for item in items]
    })


def expire_stale_cooper_runs():
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    stale = PriceSyncRun.query.filter(PriceSyncRun.status.in_(("queued", "running")), PriceSyncRun.updated_at < cutoff).all()
    for run in stale:
        run.status = "failed"
        run.error_message = "The office Cooper listener stopped responding for more than 30 minutes."
        append_sync_log(run, run.error_message)
    if stale:
        db.session.commit()


def store_local_cooper_results(submitted, batch=None):
    if batch is None:
        batch = PriceSyncBatch(supplier="cooper", created_by_user_id=None)
        db.session.add(batch); db.session.flush()
        run = PriceSyncRun(batch=batch)
        db.session.add(run)
    else:
        run = batch.run
        for row in list(batch.rows):
            db.session.delete(row)
    run.status = "complete"; run.current = len(submitted); run.total = len(submitted); run.error_message = ""
    append_sync_log(run, "Price check complete. Review the proposed prices before applying changes.")
    matched_count = error_count = 0
    seen_item_ids = set()
    for result in submitted:
        item_id = result.get("item_id") if isinstance(result, dict) else None
        item = db.session.get(PriceItem, item_id) if isinstance(item_id, int) else None
        if item is None or item_id in seen_item_ids:
            error_count += 1; continue
        seen_item_ids.add(item_id)
        row = PriceSyncRow(batch=batch, price_item=item, part_number=item.cooper_part_number.strip(), old_price=item.price, matched_name=str(result.get("name") or "")[:255])
        submitted_part = " ".join(str(result.get("part_number") or "").casefold().split())
        expected_part = " ".join(item.cooper_part_number.casefold().split())
        if result.get("status") == "error":
            row.status = "error"; row.message = str(result.get("error") or "Local scanner could not read this item.")[:255]; error_count += 1
        elif submitted_part != expected_part:
            row.status = "error"; row.message = "Returned part number did not exactly match the Pricing Sheet."; error_count += 1
        else:
            try:
                row.new_price, row.message = calculate_local_cooper_price(item, result.get("source_price"), result.get("source_quantity", 1), result.get("source_unit"))
                row.status = "matched"; matched_count += 1
            except ValueError as exc:
                row.status = "error"; row.message = str(exc)[:255]; error_count += 1
        db.session.add(row)
    db.session.commit()
    return batch, matched_count, error_count


@app.route("/api/local-cooper/commands/next", methods=["GET"])
@local_cooper_api_required
def local_cooper_next_command():
    expire_stale_cooper_runs()
    running = PriceSyncRun.query.filter_by(status="running").order_by(PriceSyncRun.updated_at.desc()).first()
    if running:
        return jsonify({"command": None, "state": "busy", "batch_id": running.batch_id})
    run = PriceSyncRun.query.filter_by(status="queued").order_by(PriceSyncRun.id).first()
    if run is None:
        return jsonify({"command": None, "state": "idle"})
    run.status = "running"; run.current = 0
    append_sync_log(run, "Office listener accepted the command and is opening Cooper.")
    db.session.commit()
    item_query = PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.cooper_part_number != "")
    if run.only_zero_prices:
        item_query = item_query.filter(PriceItem.price <= 0)
    if run.mode == "catalog":
        version = re.search(r"Updater/([\d.]+)", request.headers.get("User-Agent", ""))
        put_listener_version(version.group(1) if version else "1")
        if not version or tuple(int(x) for x in version.group(1).split(".")[:2]) < (2, 1):
            run.status = "failed"; run.error_message = "This listener is too old to read Cooper's catalog: download v2.1 or newer from Settings."
            append_sync_log(run, run.error_message); db.session.commit()
            return jsonify({"command": None, "state": "idle"})
        return jsonify({"state": "running", "command": {"id": run.batch_id, "mode": "catalog", "items": [], "limit": run.limit_items,
                                                         "known_parts": sorted(known_cooper_parts()), "skip_categories": catalog_done()}})
    if run.item_ids_json:
        item_query = item_query.filter(PriceItem.id.in_(run.item_ids or [0]))
    items = item_query.order_by(PriceItem.name).all()
    version = re.search(r"Updater/([\d.]+)", request.headers.get("User-Agent", ""))
    put_listener_version(version.group(1) if version else "1")
    return jsonify({"state":"running", "command":{"id":run.batch_id, "items":[{"id":item.id, "name":item.name, "part_number":item.cooper_part_number.strip(), "unit":item.unit, "current_price":item.price} for item in items]}})


@app.route("/api/local-cooper/commands/<int:batch_id>/heartbeat", methods=["POST"])
@local_cooper_api_required
def local_cooper_heartbeat(batch_id):
    batch = db.get_or_404(PriceSyncBatch, batch_id); run = batch.run
    if run is None or run.status not in ("queued", "running"):
        return jsonify({"error":"This command is no longer active."}), 409
    payload = request.get_json(silent=True) or {}
    run.status = "running"
    if isinstance(payload.get("current"), int): run.current = max(payload["current"], 0)
    if isinstance(payload.get("total"), int): run.total = max(payload["total"], 0)
    message = str(payload.get("message") or "").strip()
    if message: append_sync_log(run, message[:500])
    run.updated_at = datetime.utcnow(); db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/local-cooper/commands/<int:batch_id>/failed", methods=["POST"])
@local_cooper_api_required
def local_cooper_command_failed(batch_id):
    batch = db.get_or_404(PriceSyncBatch, batch_id); run = batch.run
    payload = request.get_json(silent=True) or {}; message = str(payload.get("error") or "The local Cooper listener stopped unexpectedly.")[:255]
    if run:
        run.status = "failed"; run.error_message = message; append_sync_log(run, f"Stopped: {message}"); db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/local-cooper/commands/<int:batch_id>/results", methods=["POST"])
@local_cooper_api_required
def local_cooper_command_results(batch_id):
    batch = db.get_or_404(PriceSyncBatch, batch_id)
    if batch.run is None or batch.run.status not in ("queued", "running"):
        return jsonify({"error":"This command is no longer accepting results."}), 409
    payload = request.get_json(silent=True) or {}; submitted = payload.get("results")
    if not isinstance(submitted, list) or not submitted or len(submitted) > 1000:
        return jsonify({"error":"Submit between 1 and 1,000 results."}), 400
    batch, matched, errors = store_local_cooper_results(submitted, batch)
    return jsonify({"batch_id":batch.id, "matched":matched, "errors":errors, "preview_url":url_for("cooper_sync_preview", batch_id=batch.id, _external=True)}), 201


@app.route("/api/local-cooper/commands/<int:batch_id>/image", methods=["POST"])
@local_cooper_api_required
def local_cooper_command_image(batch_id):
    """A product picture the listener captured while scanning. Saved at once (pictures need no price review),
    never over a picture someone uploaded by hand."""
    batch = db.get_or_404(PriceSyncBatch, batch_id)
    if batch.run is None or batch.run.status not in ("queued", "running"):
        return jsonify({"error": "This command is no longer active."}), 409
    payload = request.get_json(silent=True) or {}
    item = db.session.get(PriceItem, payload.get("item_id")) if isinstance(payload.get("item_id"), int) else None
    if item is None or " ".join(str(payload.get("part_number") or "").casefold().split()) != " ".join(item.cooper_part_number.casefold().split()):
        return jsonify({"error": "That picture does not match a Pricing Sheet item's Cooper part number."}), 400
    if item.image_source == "upload":
        return jsonify({"ok": True, "kept": "upload"})
    try:
        data = base64.b64decode(str(payload.get("data") or ""), validate=True)
    except (ValueError, TypeError):
        return jsonify({"error": "The picture was not valid base64."}), 400
    error = save_price_item_image(item, data, "cooper")
    if error:
        return jsonify({"error": error}), 400
    db.session.commit()
    return jsonify({"ok": True, "image": item.image_path}), 201


LISTENER_FILE = os.path.join(basedir, "tools", "cooper_price_listener.py")


def listener_latest_version():
    try:
        with open(LISTENER_FILE, encoding="utf-8") as handle:
            found = re.search(r'^LISTENER_VERSION = "([\d.]+)"', handle.read(), re.MULTILINE)
            return found.group(1) if found else ""
    except OSError:
        return ""


def put_listener_version(version):
    row = db.session.get(CompanySetting, "cooper_listener_version") or CompanySetting(key="cooper_listener_version")
    row.value = version
    db.session.add(row)
    db.session.commit()


@app.route("/settings/local-cooper/listener.py")
@login_required
def download_cooper_listener():
    return send_from_directory(os.path.join(basedir, "tools"), "cooper_price_listener.py", as_attachment=True,
                               download_name="cooper_price_listener.py", mimetype="text/x-python")


# ---------------- Cooper's catalog -> Unconfirmed items ----------------
UNCONFIRMED = "Unconfirmed"
CATALOG_BATCH, CATALOG_BATCH_MAX = 5000, 20000        # new items one "Add all items from Cooper" run adds before it stops
CATALOG_UNITS = {"ea", "ft", "box", "roll", "lb"}


def known_cooper_parts():
    """Every part number already on the Pricing Sheet (active or not), squashed for comparing."""
    parts = {canonical_part_number(row[0]) for row in db.session.query(PriceItem.cooper_part_number).filter(PriceItem.cooper_part_number != "")}
    parts |= {canonical_part_number(row[0]) for row in db.session.query(SupplierPartNumber.part_number)}
    return {p for p in parts if p}


def catalog_done():
    try:
        value = json.loads((db.session.get(CompanySetting, "cooper_catalog_done") or CompanySetting(value="[]")).value or "[]")
        return value if isinstance(value, list) else []
    except ValueError:
        return []


def add_catalog_product(product, known):
    """One product from Cooper's catalog as an Unconfirmed Pricing Sheet item. Returns the item, or None (known/unusable)."""
    part = str(product.get("part_number") or "").strip()[:100]
    key = canonical_part_number(part)
    if not key or key in known:
        return None
    try:
        price = float(product.get("source_price"))
        quantity = float(product.get("source_quantity") or 1)
    except (TypeError, ValueError):
        return None
    unit = normalize_pricing_unit(product.get("source_unit"))
    if price < 0 or quantity <= 0 or unit not in CATALOG_UNITS:
        return None
    name = " ".join(str(product.get("name") or part).split())[:160] or part
    lookup = normalize_item_name(name)
    if PriceItem.query.filter_by(lookup_key=lookup).first() is not None:
        name = f"{name[:150]} ({part})"[:180]
        lookup = normalize_item_name(name)
        if PriceItem.query.filter_by(lookup_key=lookup).first() is not None:
            return None
    source_category = " ".join(str(product.get("category") or "").split())[:60]
    item = PriceItem(name=name, lookup_key=lookup, category=UNCONFIRMED, unit=unit, price=round(price / quantity, 5),
                     labor_hours_per_unit=0, cooper_part_number=part, active=True,
                     notes=f"Added from Cooper's catalog {datetime.utcnow():%b %-d, %Y}" + (f" · Cooper: {source_category}" if source_category else ""))
    item.supplier_parts.append(SupplierPartNumber(supplier="Cooper Electric", part_number=part))
    db.session.add(item)
    known.add(key)
    return item


@app.route("/quoting/pricing/cooper-catalog", methods=["POST"])
@login_required
def start_cooper_catalog():
    """"Add all items from Cooper": queue a catalog crawl for the office listener."""
    check_csrf()
    expire_stale_cooper_runs()
    active = PriceSyncRun.query.filter(PriceSyncRun.status.in_(("queued", "running"))).first()
    if active:
        return jsonify({"error": "A Cooper job is already queued or running.", "batch_id": active.batch_id,
                        "status_url": url_for("cooper_sync_status", batch_id=active.batch_id)}), 409
    if request.form.get("restart"):
        row = db.session.get(CompanySetting, "cooper_catalog_done") or CompanySetting(key="cooper_catalog_done")
        row.value = "[]"; db.session.add(row)
    try:
        limit = min(max(int(request.form.get("batch") or CATALOG_BATCH), 1), CATALOG_BATCH_MAX)
    except ValueError:
        limit = CATALOG_BATCH
    batch = PriceSyncBatch(supplier="cooper", created_by=current_user)
    db.session.add(batch); db.session.flush()
    done = len(catalog_done())
    db.session.add(PriceSyncRun(batch=batch, mode="catalog", total=0, limit_items=limit, activity_log=json.dumps([
        f"Catalog run queued for up to {limit} new items: the office listener reads Cooper's categories and adds part numbers that are not "
        "already on the Pricing Sheet, as Unconfirmed." + (f" {done} categories finished earlier are skipped." if done else "")])))
    db.session.commit()
    return jsonify({"batch_id": batch.id, "status_url": url_for("cooper_sync_status", batch_id=batch.id),
                    "preview_url": url_for("pricing_sheet", category=UNCONFIRMED)})


@app.route("/api/local-cooper/commands/<int:batch_id>/catalog", methods=["POST"])
@local_cooper_api_required
def local_cooper_catalog(batch_id):
    """Products found by a catalog run, in batches. Only part numbers not already on the sheet are added."""
    batch = db.get_or_404(PriceSyncBatch, batch_id); run = batch.run
    if run is not None and run.mode == "catalog" and run.status == "complete" and run.limit_items and run.current >= run.limit_items:
        return jsonify({"added": 0, "skipped": 0, "total_added": run.current, "stop": True}), 200
    if run is None or run.mode != "catalog" or run.status not in ("queued", "running"):
        return jsonify({"error": "This catalog run is no longer active."}), 409
    payload = request.get_json(silent=True) or {}
    products = payload.get("products") or []
    if not isinstance(products, list) or len(products) > 500:
        return jsonify({"error": "Send up to 500 products at a time."}), 400
    known = known_cooper_parts()
    room = (run.limit_items - run.current) if run.limit_items else len(products)
    added = 0
    for product in products:
        if added >= room:
            break                              # this run's batch is full: the rest are found again next run
        if isinstance(product, dict) and add_catalog_product(product, known) is not None:
            added += 1
    done = catalog_done()
    for url in payload.get("categories_done") or []:
        if isinstance(url, str) and url not in done:
            done.append(url[:300])
    row = db.session.get(CompanySetting, "cooper_catalog_done") or CompanySetting(key="cooper_catalog_done")
    row.value = json.dumps(done); db.session.add(row)
    run.status = "running"; run.current += added
    if isinstance(payload.get("categories_total"), int):
        run.total = payload["categories_total"]
    message = str(payload.get("message") or "").strip()
    if added or message:
        append_sync_log(run, (f"Added {added} new item{'s' if added != 1 else ''}. " if added else "") + message[:300])
    full = bool(run.limit_items) and run.current >= run.limit_items
    if full and run.status != "complete":
        run.status = "complete"
        append_sync_log(run, f"This run's batch is full: {run.current} new Unconfirmed items added. Press Add all items from Cooper again for the next {run.limit_items}.")
    elif payload.get("finished") and run.status != "complete":
        run.status = "complete"
        append_sync_log(run, f"Catalog run finished: {run.current} new Unconfirmed item{'s' if run.current != 1 else ''} on the Pricing Sheet.")
    run.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"added": added, "skipped": len(products) - added, "total_added": run.current, "stop": full}), 201


@app.route("/quoting/pricing/unconfirmed-switch", methods=["POST"])
@login_required
def unconfirmed_switch():
    check_csrf()
    row = db.session.get(CompanySetting, "caden_unconfirmed") or CompanySetting(key="caden_unconfirmed")
    row.value = "1" if request.form.get("on") == "1" else "0"
    db.session.add(row); db.session.commit()
    flash("Caden will " + ("use matching Unconfirmed items (up to 60 per quote)." if row.value == "1" else "not use Unconfirmed items.") , "success")
    return redirect(url_for("pricing_sheet"))


@app.route("/quoting/pricing/move-selected", methods=["POST"])
@login_required
def move_selected_price_items():
    check_csrf()
    category = " ".join(request.form.get("category", "").split())[:100]
    ids = {int(v) for v in request.form.get("item_ids", "").split(",") if v.strip().isdigit()}
    if not category or not ids:
        flash("Select items and type a category.", "error")
        return redirect(request.referrer or url_for("pricing_sheet"))
    moved = PriceItem.query.filter(PriceItem.id.in_(ids), PriceItem.active.is_(True)).update({"category": category}, synchronize_session=False)
    db.session.commit()
    flash(f"Moved {moved} item{'s' if moved != 1 else ''} to {category}.", "success")
    return redirect(request.referrer or url_for("pricing_sheet"))


def caden_uses_unconfirmed():
    row = db.session.get(CompanySetting, "caden_unconfirmed")
    return row is None or row.value != "0"


def relevant_unconfirmed(texts, limit=60):
    """The Unconfirmed items whose names share the most words with the job and the conversation."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", " ".join(texts).lower()) if w not in SCOPE_FILLER}
    if not words:
        return []
    scored = []
    for item in PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.category == UNCONFIRMED).all():
        name_words = set(re.findall(r"[a-z0-9]{3,}", item.name.lower()))
        score = len(words & name_words)
        if score:
            scored.append((score, item.name, item))
    return [item for score, name, item in sorted(scored, key=lambda row: (-row[0], row[1]))[:limit]]


@app.route("/api/local-cooper/results", methods=["POST"])
@local_cooper_api_required
def local_cooper_results():
    payload = request.get_json(silent=True) or {}
    submitted = payload.get("results")
    if not isinstance(submitted, list) or not submitted:
        return jsonify({"error": "Submit a non-empty results list."}), 400
    if len(submitted) > 1000:
        return jsonify({"error": "A single price batch cannot exceed 1,000 items."}), 400

    batch, matched_count, error_count = store_local_cooper_results(submitted)
    return jsonify({
        "batch_id": batch.id,
        "matched": matched_count,
        "errors": error_count,
        "preview_url": url_for("cooper_sync_preview", batch_id=batch.id, _external=True),
    }), 201


@app.route("/settings/roles")
@login_required
def roles():
    return render_template("roles.html", roles=Role.query.order_by(Role.name).all())


@app.route("/settings/roles/<int:role_id>", methods=["POST"])
@login_required
def update_role(role_id):
    check_csrf(); role = db.get_or_404(Role, role_id)
    if role.is_admin:
        flash("Administrator permissions always remain enabled.", "error"); return redirect(url_for("roles"))
    fields = ["view_quoting","view_pricing","view_services","view_clients","view_jobs","view_schedule","view_all_schedules","view_sales","view_time","view_all_timesheets","view_tasks","view_training","view_finance","view_expenses"]
    for field in fields: setattr(role, field, request.form.get(field) == "on")
    db.session.commit(); flash(f"Saved {role.name} permissions.", "success")
    return redirect(url_for("roles"))


@app.route("/settings/agents/new", methods=["POST"])
@login_required
def create_agent():
    check_csrf()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Agent name is required.", "error")
        return redirect(url_for("settings"))
    agent = AiAgent(name=name, role=request.form.get("role", "").strip() or "AI Agent", identity=request.form.get("identity", "").strip())
    db.session.add(agent)
    db.session.commit()
    flash(f'Created agent "{name}".', "success")
    return redirect(url_for("edit_agent", agent_id=agent.id))


@app.route("/settings/agents/<int:agent_id>", methods=["GET", "POST"])
@login_required
def edit_agent(agent_id):
    agent = db.get_or_404(AiAgent, agent_id)
    if request.method == "POST":
        check_csrf()
        agent.name = request.form.get("name", "").strip() or agent.name
        agent.role = request.form.get("role", "").strip() or "AI Agent"
        agent.identity = request.form.get("identity", "").strip()
        agent.prompt = request.form.get("prompt", "").strip()
        agent.active = request.form.get("active") == "on"
        db.session.commit()
        flash(f'Saved {agent.name}.', "success")
        return redirect(url_for("edit_agent", agent_id=agent.id))
    return render_template("agent_form.html", agent=agent)


@app.route("/settings/agents/<int:agent_id>/knowledge", methods=["POST"])
@login_required
def add_agent_knowledge(agent_id):
    check_csrf()
    agent = db.get_or_404(AiAgent, agent_id)
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    if not title or not content:
        flash("Knowledge title and content are required.", "error")
    else:
        db.session.add(AgentKnowledge(agent=agent, title=title, category=request.form.get("category", "").strip() or "General", content=content))
        db.session.commit()
        flash("Knowledge added.", "success")
    return redirect(url_for("edit_agent", agent_id=agent.id))


@app.route("/settings/knowledge/<int:entry_id>/edit", methods=["POST"])
@login_required
def edit_agent_knowledge(entry_id):
    check_csrf()
    entry = db.get_or_404(AgentKnowledge, entry_id)
    entry.title = request.form.get("title", "").strip() or entry.title
    entry.category = request.form.get("category", "").strip() or "General"
    entry.content = request.form.get("content", "").strip() or entry.content
    entry.active = request.form.get("active") == "on"
    db.session.commit()
    flash("Knowledge entry updated.", "success")
    return redirect(url_for("edit_agent", agent_id=entry.agent_id))


@app.route("/settings/knowledge/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_agent_knowledge(entry_id):
    check_csrf()
    entry = db.get_or_404(AgentKnowledge, entry_id)
    agent_id = entry.agent_id
    db.session.delete(entry)
    db.session.commit()
    flash("Knowledge entry deleted.", "success")
    return redirect(url_for("edit_agent", agent_id=agent_id))


@app.route("/settings/agents/<int:agent_id>/files", methods=["POST"])
@login_required
def upload_agent_file(agent_id):
    check_csrf()
    agent = db.get_or_404(AiAgent, agent_id)
    upload = request.files.get("reference_file")
    if not upload or not upload.filename:
        flash("Choose a reference file.", "error")
        return redirect(url_for("edit_agent", agent_id=agent.id))
    extension = os.path.splitext(upload.filename)[1].lower()
    if extension not in {".txt", ".md", ".csv", ".pdf"}:
        flash("Reference files must be TXT, MD, CSV, or PDF.", "error")
        return redirect(url_for("edit_agent", agent_id=agent.id))
    upload_dir = os.path.join(basedir, "static", "uploads", "agents", str(agent.id))
    os.makedirs(upload_dir, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}-{secure_filename(upload.filename)}"
    path = os.path.join(upload_dir, stored_name)
    upload.save(path)
    try:
        extracted = extract_reference_text(path, extension)
    except Exception:
        app.logger.exception("Reference extraction failed")
        extracted = ""
    db.session.add(AgentReferenceFile(agent=agent, original_name=upload.filename, stored_name=stored_name, extracted_text=extracted))
    db.session.commit()
    flash("Reference uploaded and indexed for this agent." if extracted else "File uploaded, but no readable text was found.", "success" if extracted else "error")
    return redirect(url_for("edit_agent", agent_id=agent.id))


@app.route("/settings/agent-files/<int:file_id>/delete", methods=["POST"])
@login_required
def delete_agent_file(file_id):
    check_csrf()
    reference = db.get_or_404(AgentReferenceFile, file_id)
    agent_id = reference.agent_id
    path = os.path.join(basedir, "static", "uploads", "agents", str(agent_id), reference.stored_name)
    if os.path.isfile(path):
        os.remove(path)
    db.session.delete(reference)
    db.session.commit()
    flash("Reference file removed.", "success")
    return redirect(url_for("edit_agent", agent_id=agent_id))


@app.route("/settings/add-user", methods=["POST"])
@login_required
def add_user():
    check_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    error = validate_new_account(username, password, confirm)
    if error:
        flash(error, "error")
        return redirect(url_for("settings"))

    role = db.session.get(Role, request.form.get("role_id", type=int)) or Role.query.filter_by(name="Installer").first()
    user = User(username=username, role=role, hourly_rate=max(request.form.get("hourly_rate", type=float) or 0, 0))
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'Added user "{username}".', "success")
    return redirect(url_for("settings"))


@app.route("/settings/users/<int:user_id>", methods=["POST"])
@login_required
def update_user(user_id):
    check_csrf(); user = db.get_or_404(User, user_id)
    role = db.session.get(Role, request.form.get("role_id", type=int))
    if role: user.role = role
    user.hourly_rate = max(request.form.get("hourly_rate", type=float) or 0, 0)
    db.session.commit(); flash(f"Updated {user.username}.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/reset-password/<int:user_id>", methods=["POST"])
@login_required
def reset_password(user_id):
    check_csrf()
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if len(password) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("settings"))
    if password != confirm:
        flash("Passwords didn't match.", "error")
        return redirect(url_for("settings"))

    user.set_password(password)
    db.session.commit()
    flash(f'Password updated for "{user.username}".', "success")
    return redirect(url_for("settings"))


@app.route("/settings/delete-user/<int:user_id>", methods=["POST"])
@login_required
def delete_user(user_id):
    check_csrf()
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if User.query.count() <= 1:
        flash("Can't delete the last remaining user.", "error")
        return redirect(url_for("settings"))
    if ClockSession.query.filter_by(user_id=user.id).first():
        flash("This user has timesheet history and cannot be removed. Keep the account for payroll records.", "error")
        return redirect(url_for("settings"))

    was_self = user.id == current_user.id
    username = user.username
    Job.query.filter_by(assigned_user_id=user.id).update({"assigned_user_id": None})
    Quote.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None})
    WorkTask.query.filter_by(assigned_user_id=user.id).update({"assigned_user_id": None})
    WorkTask.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None})
    Training.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None})
    BugReport.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None})
    JobMaterialOrder.query.filter_by(selected_by_user_id=user.id).update({"selected_by_user_id": None})
    db.session.delete(user)
    db.session.commit()
    flash(f'Removed user "{username}".', "success")

    if was_self:
        logout_user()
        return redirect(url_for("login"))
    return redirect(url_for("settings"))


# ---------------- Helpers ----------------

def validate_new_account(username, password, confirm):
    if not username or len(username) < 3:
        return "Username must be at least 3 characters."
    if User.query.filter_by(username=username).first():
        return f'The username "{username}" is already taken.'
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if password != confirm:
        return "Passwords didn't match."
    return None


def normalize_item_name(name):
    return " ".join(name.casefold().split())


def canonical_item_name(name):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(name or "").casefold()).split())


def canonical_part_number(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def find_near_duplicate_price_items(items, limit=60):
    """Find likely naming mistakes without merging or changing any records."""
    pairs = []
    for position, left in enumerate(items):
        left_key = canonical_item_name(left.name)
        left_tokens = set(left_key.split())
        if not left_key:
            continue
        for right in items[position + 1:]:
            right_key = canonical_item_name(right.name)
            if not right_key:
                continue
            right_tokens = set(right_key.split())
            ratio = SequenceMatcher(None, left_key, right_key).ratio()
            contained = left_key in right_key or right_key in left_key
            overlap = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
            same_part = any(
                canonical_item_name(a.supplier) == canonical_item_name(b.supplier)
                and canonical_part_number(a.part_number)
                and canonical_part_number(a.part_number) == canonical_part_number(b.part_number)
                for a in left.supplier_parts for b in right.supplier_parts
            )
            if same_part:
                score, reason = 1.0, "Same supplier part number"
            elif contained and min(len(left_key), len(right_key)) >= 3:
                score, reason = max(.9, ratio), "One name contains the other"
            elif ratio >= .74 or (overlap >= .75 and bool(left_tokens & right_tokens)):
                score, reason = max(ratio, overlap * .9), "Names are very similar"
            else:
                continue
            pairs.append({"left": left, "right": right, "score": round(score * 100), "reason": reason})
    pairs.sort(key=lambda pair: (-pair["score"], canonical_item_name(pair["left"].name), canonical_item_name(pair["right"].name)))
    return pairs[:limit]


def find_duplicate_price_item(name, cooper_part_number="", exclude_id=None, include_inactive=True, supplier_parts=None):
    name_key = canonical_item_name(name)
    part_key = canonical_part_number(cooper_part_number)
    query = PriceItem.query
    if not include_inactive:
        query = query.filter_by(active=True)
    for candidate in query.order_by(PriceItem.id).all():
        if exclude_id and candidate.id == exclude_id:
            continue
        if name_key and canonical_item_name(candidate.name) == name_key:
            return candidate
        if part_key and canonical_part_number(candidate.cooper_part_number) == part_key:
            return candidate
        for supplier, supplier_part in supplier_parts or []:
            supplier_key = canonical_item_name(supplier)
            supplier_part_key = canonical_part_number(supplier_part)
            if supplier_part_key and any(canonical_item_name(row.supplier) == supplier_key and canonical_part_number(row.part_number) == supplier_part_key for row in candidate.supplier_parts):
                return candidate
    return None


def next_job_number():
    highest = db.session.query(db.func.max(Job.job_number)).scalar()
    return max((highest or 699) + 1, 700)


def supplier_key_path():
    return os.path.join(basedir, "instance", "supplier-credentials.key")


def supplier_cipher():
    path = supplier_key_path()
    if not os.path.exists(path):
        key = Fernet.generate_key()
        with open(path, "wb") as key_file:
            key_file.write(key)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    with open(path, "rb") as key_file:
        return Fernet(key_file.read().strip())


def encrypt_supplier_password(password):
    return supplier_cipher().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_supplier_password(encrypted):
    try:
        return supplier_cipher().decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("The stored Cooper password cannot be decrypted. Enter it again in Cooper Account.") from exc


def mask_supplier_username(username):
    if "@" not in username:
        return username[:2] + "••••" if len(username) > 2 else "••••"
    local, domain = username.split("@", 1)
    return (local[:2] + "••••@" + domain) if local else "••••@" + domain


def supplier_error_message(error):
    message = str(error).strip()
    if isinstance(error, httpx.TimeoutException): return "Cooper did not respond before the timeout."
    if isinstance(error, httpx.HTTPError): return "Cooper could not be reached from the server."
    return (message or "Unknown Cooper connection error.")[:240]


def append_sync_log(run, message):
    try: entries = json.loads(run.activity_log or "[]")
    except (TypeError, json.JSONDecodeError): entries = []
    entries.append(f"{datetime.now().strftime('%H:%M:%S')} — {message}")
    run.activity_log = json.dumps(entries[-100:])
    run.updated_at = datetime.utcnow()
    db.session.commit()


def run_cooper_price_sync(flask_app, batch_id):
    with flask_app.app_context():
        batch = db.session.get(PriceSyncBatch, batch_id)
        run = batch.run
        run.status = "running"; append_sync_log(run, "Opening Cooper's public product search…")
        try:
            with CooperBrowserPricingClient(activity=lambda message: append_sync_log(run, message)) as supplier:
                supplier.open_search()
                items = PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.cooper_part_number != "").order_by(PriceItem.name).all()
                for index, item in enumerate(items, 1):
                    append_sync_log(run, f"Searching {item.cooper_part_number.strip()} — {item.name}")
                    row = PriceSyncRow(batch=batch, price_item=item, part_number=item.cooper_part_number.strip(), old_price=item.price)
                    try:
                        match = supplier.fetch_price(row.part_number, requested_unit=item.unit)
                        if match:
                            row.new_price = match["price"]; row.matched_name = match["name"]; row.status = "matched"
                            row.message = match.get("message") or f"Found ${row.new_price:,.2f}."
                        else:
                            row.status = "not_found"; row.message = "No exact Cooper part-number match."
                            append_sync_log(run, f"Part {row.part_number}: no exact Cooper match; existing price left unchanged.")
                    except Exception as exc:
                        row.status = "error"; row.message = supplier_error_message(exc)
                        append_sync_log(run, f"Part {row.part_number} error: {row.message}")
                    db.session.add(row); run.current = index; db.session.commit()
            run.status = "complete"; append_sync_log(run, "Price check complete. Review the preview before applying changes.")
        except Exception as exc:
            message = supplier_error_message(exc)
            run.status = "failed"; run.error_message = message
            append_sync_log(run, f"Stopped: {message}")


class CooperBrowserPricingClient:
    BASE_URL = "https://www.cooper-electric.com"

    def __init__(self, username=None, password=None, activity=None):
        self.activity = activity or (lambda _message: None)
        self.playwright = self.browser = self.page = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Browser support is not installed. Run pip install -r requirements.txt.") from exc
        self.playwright = sync_playwright().start()
        chromium_path = os.environ.get("CHROMIUM_PATH") or ("/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else None)
        options = {"headless":True, "args":["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]}
        if chromium_path: options["executable_path"] = chromium_path
        self.browser = self.playwright.chromium.launch(**options)
        self.page = self.browser.new_page(viewport={"width":1280,"height":900})
        self.page.set_default_timeout(20000)
        self.activity(f"Browser started in private background mode at 1280×900 using {chromium_path or 'Playwright Chromium'}.")
        return self

    def __exit__(self, *_):
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()

    def accept_cookies(self):
        for label in ("Accept All", "Accept all", "Allow all"):
            button = self.page.get_by_role("button", name=label, exact=True)
            try:
                if button.count() and button.first.is_visible():
                    self.activity("Cookie notice found — clicking Accept All.")
                    button.first.click(); self.page.wait_for_timeout(500); return True
            except Exception:
                continue
        self.activity("Cookie notice is already accepted or was not shown.")
        return False

    def first_visible(self, selectors, description):
        for selector in selectors:
            locator = self.page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    if candidate.is_visible():
                        return candidate
                except Exception:
                    continue
        raise RuntimeError(f"Cooper page loaded, but the visible {description} could not be found.")

    def open_search(self):
        self.activity(f"Navigating to {self.BASE_URL}")
        self.page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=35000)
        self.activity(f"Loaded URL: {self.page.url}")
        self.activity(f"Page title: {self.page.title() or '(no title)'}")
        self.accept_cookies()
        self.activity("Cooper's public catalog is ready — no sign-in or search-bar lookup required.")
        return True

    def login(self):
        return self.open_search()

    @staticmethod
    def parse_search_results(html, part_number, requested_unit="ea"):
        soup = BeautifulSoup(html, "html.parser")
        target = " ".join(str(part_number).casefold().split())
        for card in soup.select("#productSearchResults div.product.productCard, div.product.productCard"):
            product_id = " ".join((card.get("data-product-id") or "").casefold().split())
            part_node = card.select_one("small.block b, small b")
            displayed_part = " ".join(part_node.get_text(" ", strip=True).casefold().split()) if part_node else ""
            if target not in {product_id, displayed_part}:
                continue

            # Cooper currently spells this class `pricerPerQty`. Prefer its
            # numeric data attribute so formatting and currency icons do not
            # affect parsing, while retaining text parsing as a fallback.
            price_node = card.select_one("span.pricerPerQty, span.pricePerQty, [data-analytics-price]")
            raw_price = (price_node.get("data-analytics-price") or "").strip() if price_node else ""
            if raw_price:
                try:
                    source_price = float(raw_price.replace(",", ""))
                except ValueError:
                    source_price = None
            else:
                price_match = re.search(r"\$\s*([0-9][0-9,]*\.\d{2})", price_node.get_text(" ", strip=True) if price_node else "")
                source_price = float(price_match.group(1).replace(",", "")) if price_match else None
            if source_price is None:
                raise RuntimeError("Exact item found, but Cooper did not provide a public price.")

            uom_texts = [node.get_text(" ", strip=True) for node in card.select("small.priceUOM")]
            uom_text = " ".join(uom_texts)
            price = source_price
            message = f"Found ${source_price:,.2f}."
            if requested_unit == "ft":
                thousand_feet = re.search(r"\(?\s*([0-9,]+)\s*ft\s*\)?", uom_text, re.IGNORECASE)
                if thousand_feet:
                    source_feet = float(thousand_feet.group(1).replace(",", ""))
                    if source_feet > 1:
                        price = source_price / source_feet
                        message = (
                            f"Cooper ${source_price:,.2f} per {source_feet:,.0f} ft "
                            f"→ ${price:,.5f} per ft."
                        )

            heading = card.select_one("[data-analytics-description]") or card.find(["h2", "h3", "h4"]) or card.select_one(".upper a") or card.find("a")
            name = (heading.get("data-analytics-description") or heading.get_text(" ", strip=True)) if heading else str(part_number)
            return {
                "price": price,
                "source_price": source_price,
                "source_uom": uom_text,
                "name": name,
                "message": message,
            }
        return None

    def fetch_price(self, part_number, requested_unit="ea"):
        # Cooper's public header form submits a GET request to /product/search.
        # Navigate to that endpoint directly so header, responsive-layout, or
        # cookie-banner changes cannot prevent a price check from starting.
        search_url = f"{self.BASE_URL}/product/search?q={quote_plus(str(part_number).strip())}"
        self.activity(f"Part {part_number}: Pricing Sheet unit is '{requested_unit}'.")
        self.activity(f"Navigating directly to {search_url}")
        self.page.goto(search_url, wait_until="domcontentloaded", timeout=35000)
        self.activity(f"Loaded URL: {self.page.url}")
        self.activity(f"Page title: {self.page.title() or '(no title)'}")
        self.accept_cookies()
        cards = self.page.locator("#productSearchResults div.product.productCard")
        self.activity("Waiting up to 20 seconds for Cooper product results to appear…")
        try:
            cards.first.wait_for(state="attached", timeout=20000)
        except Exception as exc:
            visible_text = " ".join(self.page.locator("body").inner_text().split())[:240]
            raise RuntimeError(
                f"No product cards appeared within 20 seconds. URL: {self.page.url[:160]}; "
                f"title: {self.page.title()[:80]}; page text: {visible_text or '(blank page)'}"
            ) from exc
        if "/product/search" not in self.page.url:
            raise RuntimeError(
                f"Cooper redirected the public search to {self.page.url[:160]} "
                f"(page title: {self.page.title()[:80]})."
            )
        card_count = cards.count()
        self.activity(f"Cooper returned {card_count} product card{'s' if card_count != 1 else ''}.")
        target = " ".join(str(part_number).casefold().split())
        exact_card = None
        observed_ids = []
        for index in range(card_count):
            candidate = cards.nth(index)
            product_id = " ".join((candidate.get_attribute("data-product-id") or "").casefold().split())
            if product_id:
                observed_ids.append(product_id)
            if product_id == target:
                exact_card = candidate
                break
        if exact_card is None:
            sample = ", ".join(observed_ids[:8]) or "none"
            self.activity(f"No exact data-product-id match for {part_number}. First returned IDs: {sample}.")
            return None

        self.activity(f"Exact match found: data-product-id='{part_number}'.")
        price_node = exact_card.locator("span.pricerPerQty, span.pricePerQty, [data-analytics-price]").first
        self.activity("Waiting up to 20 seconds for Cooper's price field to load inside the exact product card…")
        try:
            price_node.wait_for(state="attached", timeout=20000)
            raw_price = ""
            for _ in range(40):
                raw_price = (price_node.get_attribute("data-analytics-price") or "").strip()
                if raw_price:
                    break
                self.page.wait_for_timeout(500)
        except Exception as exc:
            raise RuntimeError(
                f"Exact part {part_number} appeared, but its price field did not load within 20 seconds. "
                f"URL: {self.page.url[:160]}"
            ) from exc
        if not raw_price:
            raise RuntimeError(f"Exact part {part_number} appeared, but data-analytics-price remained blank after 20 seconds.")

        uom_text = " ".join(exact_card.locator("small.priceUOM").all_text_contents()).strip()
        self.activity(f"Raw Cooper price field: {raw_price}; displayed unit: {uom_text or '(not supplied)'}.")
        result = self.parse_search_results(self.page.content(), part_number, requested_unit=requested_unit)
        if result is None:
            raise RuntimeError(
                f"The browser found exact card {part_number}, but the HTML parser could not read it. "
                f"URL: {self.page.url[:160]}"
            )
        self.activity(result["message"])
        self.activity(f"Part {part_number} is ready for preview; no Pricing Sheet value has been changed yet.")
        return result


class CooperPricingClient:
    BASE_URL = "https://www.cooper-electric.com"

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.client = httpx.Client(base_url=self.BASE_URL, follow_redirects=True, timeout=25, headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36","Accept-Language":"en-US,en;q=0.9"})

    def __enter__(self): return self
    def __exit__(self, *_): self.client.close()

    def login(self):
        page = self.client.get("/account/login"); page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        form = next((form for form in soup.find_all("form") if form.get("action", "").rstrip("/").endswith("/account/login") and form.find("input", {"name":"Password"})), None)
        if form is None: raise RuntimeError("Cooper changed its login page or blocked automated access.")
        data = {field.get("name"): field.get("value", "") for field in form.find_all("input") if field.get("name") and field.get("type") == "hidden"}
        data.update({"UserName":self.username,"Password":self.password,"Remember":"false"})
        response = self.client.post(form.get("action") or "/account/login", data=data); response.raise_for_status()
        result = BeautifulSoup(response.text, "html.parser"); text_content = result.get_text(" ", strip=True).casefold()
        still_login = result.find("input", {"name":"Password"}) is not None and result.find("input", {"name":"UserName"}) is not None
        if still_login or ("log out" not in text_content and "my account" not in text_content):
            raise RuntimeError("Cooper rejected the login. Re-enter the current username and password.")
        return True

    def fetch_price(self, part_number):
        response = self.client.get("/product/search", params={"q":part_number}); response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        target = " ".join(str(part_number).casefold().split())
        for card in soup.select("div.product.productCard"):
            part_text = ""
            for element in card.find_all(["small","div","span"]):
                candidate = element.get_text(" ", strip=True)
                if candidate.casefold().startswith("cooper electric part #:"):
                    part_text = candidate.split(":", 1)[1].strip(); break
            if " ".join(part_text.casefold().split()) != target: continue
            card_text = card.get_text(" ", strip=True)
            price_match = re.search(r"\$\s*([0-9][0-9,]*\.\d{2})\s*/", card_text)
            if not price_match: raise RuntimeError("Exact item found, but Cooper did not provide an account price.")
            heading = card.find(["h2","h3","h4"]) or card.select_one(".upper a") or card.find("a")
            return {"price":float(price_match.group(1).replace(",","")),"name":heading.get_text(" ", strip=True) if heading else part_text}
        return None


def get_labor_rate():
    setting = db.session.get(CompanySetting, "labor_rate")
    try:
        return float(setting.value) if setting else 361.90
    except (TypeError, ValueError):
        return 361.90


DEFAULT_DISPATCH_ADDRESS = "1385 Old Reading Road, Paxinos, PA 17860"


def get_dispatch_address():
    setting = db.session.get(CompanySetting, "dispatch_address")
    return (setting.value.strip() if setting and setting.value.strip() else DEFAULT_DISPATCH_ADDRESS)


def get_numeric_setting(key, default):
    setting = db.session.get(CompanySetting, key)
    try:
        return float(setting.value) if setting else default
    except (TypeError, ValueError):
        return default


def geocode_us_address(address):
    response = httpx.get(
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
        params={"address":address, "benchmark":"Public_AR_Current", "format":"json"},
        headers={"User-Agent":"PowerHouseFlow/1.0 (office@powerhouse-pa.com)"},
        timeout=25,
    )
    response.raise_for_status()
    matches = response.json().get("result", {}).get("addressMatches", [])
    if not matches:
        raise RuntimeError(f'The free Census geocoder could not locate "{address}". Enter a manual one-way time on the job.')
    coordinates = matches[0].get("coordinates", {})
    return float(coordinates["y"]), float(coordinates["x"])


def calculate_estimated_drive_time(destination):
    origin_lat, origin_lon = geocode_us_address(get_dispatch_address())
    destination_lat, destination_lon = geocode_us_address(destination)
    lat1, lat2 = math.radians(origin_lat), math.radians(destination_lat)
    delta_lat = math.radians(destination_lat - origin_lat)
    delta_lon = math.radians(destination_lon - origin_lon)
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    haversine = min(max(haversine, 0), 1)
    straight_miles = 3958.7613 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    road_factor = max(get_numeric_setting("travel_road_factor", 1.25), 1)
    average_speed = max(get_numeric_setting("travel_average_speed", 45), 1)
    estimated_road_miles = straight_miles * road_factor
    return (estimated_road_miles / average_speed) * 60, estimated_road_miles


def refresh_job_drive_time(job, force=False):
    if not job.job_address:
        raise RuntimeError("The job requires a complete work address before drive time can be calculated.")
    if job.drive_minutes_override is not None:
        job.drive_minutes = job.drive_minutes_override; job.drive_error = ""; job.drive_checked_at = datetime.utcnow()
        return job.drive_minutes
    if not force and job.drive_minutes is not None and job.drive_checked_at and job.drive_checked_at > datetime.utcnow() - timedelta(days=30):
        return job.drive_minutes
    try:
        job.drive_minutes, job.drive_distance_miles = calculate_estimated_drive_time(job.job_address)
        job.drive_checked_at = datetime.utcnow(); job.drive_error = ""
    except Exception as exc:
        job.drive_minutes = None; job.drive_distance_miles = None; job.drive_checked_at = datetime.utcnow()
        job.drive_error = " ".join(str(exc).split())[:255]
        raise
    return job.drive_minutes


def refresh_quote_travel(quote, force=False):
    minutes = refresh_job_drive_time(quote.job, force=force)
    quote.travel_minutes_snapshot = minutes
    quote.travel_origin_snapshot = get_dispatch_address()
    quote.travel_destination_snapshot = quote.job.job_address
    quote.travel_method_snapshot = "manual" if quote.job.drive_minutes_override is not None else "estimated"
    quote.travel_cost_snapshot = (minutes / 60) * get_labor_rate() if minutes > 60 else 0
    return quote.travel_cost_snapshot


def set_company_setting(key, value):
    setting = db.session.get(CompanySetting, key) or CompanySetting(key=key)
    setting.value = str(value)
    db.session.add(setting)
    db.session.commit()


def extract_reference_text(path, extension):
    if extension == ".pdf":
        from pypdf import PdfReader
        text_content = "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as reference_file:
            text_content = reference_file.read()
    return text_content.strip()[:80000]


def build_agent_context(agent):
    if agent is None:
        return ""
    sections = [f"AGENT IDENTITY — {agent.name} ({agent.role}):\n{agent.identity}"]
    if agent.prompt:
        sections.append(f"EDITABLE AI PROMPT:\n{agent.prompt}")
    for entry in agent.knowledge_entries:
        if entry.active:
            sections.append(f"KNOWLEDGE — {entry.category} / {entry.title}:\n{entry.content}")
    for reference in agent.reference_files:
        if reference.extracted_text:
            sections.append(f"REFERENCE FILE — {reference.original_name}:\n{reference.extracted_text}")
    return "\n\n".join(sections)[:120000]


def generate_labor_proposals(mike, reference):
    items = PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all()
    catalog = [{"id": item.id, "name": item.name, "unit": item.unit, "current_labor_hours_per_unit": item.labor_hours_per_unit} for item in items]
    schema = {"type":"object","properties":{"proposals":{"type":"array","items":{"type":"object","properties":{"price_item_id":{"type":"integer"},"proposed_hours":{"type":"number","minimum":0},"confidence":{"type":"string","enum":["low","medium","high"]},"rationale":{"type":"string"},"source_excerpt":{"type":"string"}},"required":["price_item_id","proposed_hours","confidence","rationale","source_excerpt"],"additionalProperties":False}}},"required":["proposals"],"additionalProperties":False}
    instructions = f"""You are Mike, Power House Electrical Solutions' labor-estimating specialist.
{build_agent_context(mike)}

Analyze only the supplied labor reference and match supported labor units to exact Pricing Sheet IDs. Propose hours per the item's stored unit. Prioritize items with zero labor, but recommend a correction to a nonzero value when the reference clearly supports it. Do not invent a conversion, productivity rate, or catalog match. source_excerpt must be a short verbatim excerpt from the reference supporting the recommendation. Return only supported proposals; an empty list is valid.

PRICING SHEET:
{json.dumps(catalog)}
"""
    response = OpenAI().responses.create(model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), instructions=instructions, input=f"REFERENCE FILE — {reference.original_name}:\n{reference.extracted_text}", text={"format":{"type":"json_schema","name":"labor_rate_proposals","schema":schema,"strict":True}})
    result = json.loads(response.output_text)
    lookup = {item.id:item for item in items}; reference_key = " ".join(reference.extracted_text.split()).casefold()
    batch = LaborProposalBatch(reference_file=reference, created_by=current_user)
    db.session.add(batch)
    seen = set()
    for proposal in result.get("proposals", []):
        item = lookup.get(proposal.get("price_item_id")); excerpt = " ".join(str(proposal.get("source_excerpt", "")).split())
        if item is None or item.id in seen or not excerpt or excerpt.casefold() not in reference_key:
            continue
        hours = float(proposal.get("proposed_hours", -1))
        if hours < 0 or hours > 1000:
            continue
        seen.add(item.id)
        batch.lines.append(LaborProposalLine(price_item=item, item_name=item.name, unit=item.unit, current_hours=item.labor_hours_per_unit, proposed_hours=hours, confidence=proposal.get("confidence", "medium"), rationale=str(proposal.get("rationale", "")).strip(), source_excerpt=excerpt))
    return batch


def ask_mike_labor_question(mike, question):
    catalog = [{"name":item.name,"unit":item.unit,"labor_hours_per_unit":item.labor_hours_per_unit} for item in PriceItem.query.filter_by(active=True).order_by(PriceItem.name).all()]
    services = [{"name":service.name,"example_quantity":service.example_quantity,"labor_hours":service.estimated_labor_hours} for service in Service.query.order_by(Service.name).all()]
    instructions = f"""You are Mike, Power House Electrical Solutions' labor-time advisor.
{build_agent_context(mike)}

Answer the electrician's labor question using the uploaded references, then existing Pricing Sheet labor, then Service labor—in that order. Explain assumptions such as access, wall type, crew size, setup, and distance. Distinguish reference-backed facts from your inference. If the references do not support a reliable answer, say what is missing and give a clearly labeled planning range only when reasonable. Never change a Pricing Sheet item from chat and never discuss material selling prices.

PRICING SHEET LABOR: {json.dumps(catalog)}
SERVICE LABOR: {json.dumps(services)}
"""
    response = OpenAI().responses.create(model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), instructions=instructions, input=question)
    return response.output_text.strip()


def format_duration(seconds):
    seconds = max(int(seconds or 0), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


app.jinja_env.filters["duration"] = format_duration


def build_training_context(training):
    parts = [f"TRAINING: {training.title}", f"Provider: {training.provider}", f"Category: {training.category}", f"Date: {training.training_date or 'Not specified'}", f"Attendees: {training.attendees}", f"DESCRIPTION:\n{training.description}", f"ONGOING NOTES:\n{training.notes}"]
    for reference in training.files:
        if reference.extracted_text:
            parts.append(f"REFERENCE — {reference.original_name}:\n{reference.extracted_text}")
        elif reference.is_image:
            parts.append(f"PHOTO ATTACHED — {reference.original_name} (no written caption supplied)")
    return "\n\n".join(parts)[:120000]


def ask_darwin(training, request_text):
    if OpenAI is None:
        raise RuntimeError("The OpenAI Python package is not installed.")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("The OpenAI API key is not configured.")
    darwin = AiAgent.query.filter(db.func.lower(AiAgent.name) == "darwin", AiAgent.active.is_(True)).first()
    instructions = f"""You are Darwin, Power House Electrical Solutions' training and learning agent.
{build_agent_context(darwin)}
Answer only from the supplied training record and clearly label anything that is an inference. Do not invent code requirements, manufacturer instructions, certifications, or safety claims. Use practical, plain language for electricians and office staff.

{build_training_context(training)}"""
    recent = [{"role": message.role, "content": message.content} for message in training.messages[-10:]]
    if not recent or recent[-1]["role"] != "user" or recent[-1]["content"] != request_text:
        recent.append({"role": "user", "content": request_text})
    image_content = [{"type": "input_text", "text": request_text}]
    for reference in [file for file in training.files if file.is_image][:5]:
        path = os.path.join(basedir, "static", "uploads", "training", str(training.id), reference.stored_name)
        if os.path.isfile(path):
            with open(path, "rb") as image_file:
                encoded = base64.b64encode(image_file.read()).decode("ascii")
            image_content.append({"type": "input_image", "image_url": f"data:{reference.mime_type};base64,{encoded}"})
    if len(image_content) > 1:
        recent[-1] = {"role": "user", "content": image_content}
    response = OpenAI().responses.create(model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), instructions=instructions, input=recent)
    return response.output_text.strip()


def build_brendon_response(user_message):
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("The OpenAI API is not configured.")
    brendon = AiAgent.query.filter(db.func.lower(AiAgent.name) == "brendon", AiAgent.active.is_(True)).first()
    users = [user.username for user in User.query.order_by(User.username).all()]
    task_data = [{"task_id": task.id, "title": task.title, "description": task.description, "category": task.category, "status": task.status, "priority": task.priority, "assigned_to": task.assigned_user.username if task.assigned_user else None, "due_date": task.due_date.isoformat() if task.due_date else None} for task in WorkTask.query.filter(WorkTask.status != "completed").order_by(WorkTask.id).all()]
    instructions = f"""You are Brendon, Power House Electrical Solutions' tasking agent.
{build_agent_context(brendon)}
Translate plain-language work updates into safe task actions. You may create tasks or update/complete tasks from ACTIVE_TASKS. Never delete tasks. For an existing task use only an exact task_id supplied below. Assign only an exact username from USERS. If the person, task, due date, or intended action is ambiguous, return no action and ask one concise question. Do not claim an action happened unless you include it in actions.

USERS: {json.dumps(users)}
ACTIVE_TASKS: {json.dumps(task_data)}"""
    schema = {"type":"object","properties":{"message":{"type":"string"},"actions":{"type":"array","items":{"type":"object","properties":{"action":{"type":"string","enum":["create","update","complete"]},"task_id":{"type":["integer","null"]},"title":{"type":["string","null"]},"description":{"type":["string","null"]},"category":{"type":["string","null"]},"priority":{"type":["string","null"],"enum":["low","normal","high","urgent",None]},"assigned_to":{"type":["string","null"]},"due_date":{"type":["string","null"]}},"required":["action","task_id","title","description","category","priority","assigned_to","due_date"],"additionalProperties":False}}},"required":["message","actions"],"additionalProperties":False}
    history = [{"role": item.role, "content": item.content} for item in TaskAgentMessage.query.filter_by(user_id=current_user.id).order_by(TaskAgentMessage.created_at.desc()).limit(12).all()[::-1]]
    response = OpenAI().responses.create(model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), instructions=instructions, input=history, text={"format":{"type":"json_schema","name":"brendon_task_actions","schema":schema,"strict":True}})
    return json.loads(response.output_text)


def apply_brendon_actions(actions):
    results = []
    user_lookup = {user.username.casefold(): user for user in User.query.all()}
    for action in actions[:10]:
        kind = action.get("action")
        assigned_name = (action.get("assigned_to") or "").strip()
        assigned_user = user_lookup.get(assigned_name.casefold()) if assigned_name else None
        if assigned_name and assigned_user is None:
            results.append(f'Skipped an action because "{assigned_name}" is not an exact user.'); continue
        due_date = None
        if action.get("due_date"):
            try: due_date = date.fromisoformat(action["due_date"])
            except ValueError:
                results.append("Skipped an action because its due date was invalid."); continue
        if kind == "create":
            title = (action.get("title") or "").strip()
            if not title: continue
            task = WorkTask(title=title, description=(action.get("description") or "").strip(), category=(action.get("category") or "General").strip(), priority=action.get("priority") or "normal", assigned_user=assigned_user, due_date=due_date, created_by=current_user)
            db.session.add(task); db.session.flush(); results.append(f'Created task #{task.id}: {task.title}' + (f' for {assigned_user.username}' if assigned_user else ''))
        else:
            task = db.session.get(WorkTask, action.get("task_id")) if isinstance(action.get("task_id"), int) else None
            if task is None:
                results.append("Skipped an update because the task could not be matched."); continue
            if kind == "complete":
                task.status = "completed"; task.completed_at = datetime.utcnow(); results.append(f'Completed task #{task.id}: {task.title}')
            elif kind == "update":
                if action.get("title"): task.title = action["title"].strip()
                if action.get("description") is not None: task.description = action["description"].strip()
                if action.get("category"): task.category = action["category"].strip()
                if action.get("priority"): task.priority = action["priority"]
                if assigned_name: task.assigned_user = assigned_user
                if action.get("due_date") is not None: task.due_date = due_date
                results.append(f'Updated task #{task.id}: {task.title}')
    db.session.commit()
    return results


def validate_ai_option_progression(options, expected_tiers=None):
    expected_tiers = expected_tiers or QUOTE_TIERS
    if len(options) != len(expected_tiers) or [option.get("tier") for option in options] != expected_tiers:
        raise ValueError(f'The quote must contain exactly {len(expected_tiers)} options in this order: {", ".join(expected_tiers)}.')
    previous_signature = None
    previous_copy = None
    for option in options:
        source_signature = (
            tuple(sorted(("service", row.get("service_id"), round(float(row.get("multiplier", 0)), 4)) for row in option.get("services", []))),
            tuple(sorted(("item", row.get("price_item_id"), round(float(row.get("quantity", 0)), 4)) for row in option.get("items", []))),
            tuple(sorted(("direct", canonical_item_name(row.get("name", "")), round(float(row.get("quantity", 0)), 4), round(float(row.get("material_unit_price", 0)), 4), round(float(row.get("labor_hours_per_unit", 0)), 4)) for row in option.get("direct_priced_items", []))),
            tuple(sorted((normalize_item_name(entry[0]), round(entry[1], 4)) for entry in (_missing_entry(value) for value in option.get("missing_services", [])) if entry[0])),
        )
        copy_signature = (normalize_item_name(option.get("title", "")), tuple(normalize_item_name(item) for item in option.get("bullets", [])))
        if previous_signature is not None and source_signature == previous_signature:
            raise ValueError(f'{option.get("tier", "A higher tier")} duplicates the priced scope of the tier below it.')
        if previous_copy is not None and copy_signature == previous_copy:
            raise ValueError(f'{option.get("tier", "A higher tier")} duplicates the wording of the tier below it.')
        previous_signature = source_signature
        previous_copy = copy_signature


def build_ai_quote_response(job, quote):
    if OpenAI is None:
        raise RuntimeError("The OpenAI Python package is not installed.")
    catalog = []
    for service in Service.query.order_by(Service.name).all():
        catalog.append({
            "service_id": service.id,
            "name": service.name,
            "description": service.description,
            "example_quantity": service.example_quantity,
            "example_price": round(service.example_total, 2),
            "material_price": round(service.material_total, 2),
            "labor_hours": round(service.estimated_labor_hours, 4),
            "materials": [{"name": item.current_name, "quantity": item.quantity, "unit": item.current_unit} for item in service.items],
        })
    def _price_row(item):
        return {"price_item_id": item.id, "name": item.name, "unit": item.unit, "material_unit_price": round(item.price, 2), "labor_hours_per_unit": round(item.labor_hours_per_unit, 4), "category": item.category, "supplier_parts": [{"supplier": row.supplier, "part_number": row.part_number} for row in item.supplier_parts]}
    # Confirmed items always; from Cooper's Unconfirmed catalog only the ones matching this job (never the whole catalog)
    price_catalog = [_price_row(item) for item in PriceItem.query.filter(PriceItem.active.is_(True), PriceItem.category != UNCONFIRMED).order_by(PriceItem.name).all()]
    unconfirmed_rows = []
    if caden_uses_unconfirmed():
        texts = [job.title or "", job.description or ""] + [message.content for message in quote.messages[-8:] if message.role == "user"]
        unconfirmed_rows = [dict(_price_row(item), unconfirmed=True) for item in relevant_unconfirmed(texts)]
    conversation = [{"role": message.role, "content": message.content} for message in quote.messages[-14:]]
    agent = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden", AiAgent.active.is_(True)).first()
    agent_context = build_agent_context(agent)
    mike = AiAgent.query.filter(db.func.lower(AiAgent.name) == "mike", AiAgent.active.is_(True)).first()
    mike_context = build_agent_context(mike)[:50000] if mike else "Mike has no labor references yet."
    tiers = quote_tiers(getattr(quote, "option_count", 6))
    latest_inspection = OnsiteInspection.query.filter_by(job_id=job.id).order_by(OnsiteInspection.updated_at.desc()).first() if getattr(job, "id", None) else None
    inspection_context = {
        "home_age": latest_inspection.home_age,
        "top_seller_opportunities": latest_inspection.top_sellers,
        "checked_findings": latest_inspection.findings,
        "technician_observations": latest_inspection.observations,
        "client_priorities_observed_onsite": latest_inspection.client_priorities,
        "inspection_photos": [photo.original_name for photo in latest_inspection.photos],
    } if latest_inspection else {"status": "No onsite checklist has been saved for this job."}
    instructions = f"""You are Caden, the active quoting agent configured in Power House Flow.
{agent_context}

JOB:
{json.dumps({'title': job.title, 'client_request': job.description, 'address': job.job_address})}

ONSITE_INSPECTION:
{json.dumps(inspection_context)}

SERVICE_LIBRARY:
{json.dumps(catalog)}

PRICE_LIBRARY:
{json.dumps(price_catalog + unconfirmed_rows)}
Items marked "unconfirmed" come from Cooper's catalog: real Cooper prices that nobody here has reviewed yet. Prefer a confirmed item when one fits; use an unconfirmed one when it is the right part, and with no labor hours stored, give it a labor suggestion in pricing_needed.

MIKE_LABOR_ADVISOR:
{mike_context}

Mike's references are fallback labor guidance only. Services and Pricing Sheet labor values remain authoritative for quote math. Use Mike's knowledge to recognize labor gaps, form a conservative question, or explain assumptions; never silently replace a stored Service or Pricing Sheet labor value inside a quote.
If one missing labor duration materially affects the proposal and the stored libraries do not answer it, put one concise technical question in mike_question instead of asking the homeowner. The application will privately consult Mike once and return his answer to you. Otherwise set mike_question to an empty string. Mike's answer is advisory: it must not overwrite central labor or become deterministic quote math unless an administrator approves it into the Pricing Sheet or Service.

OPTION_COUNT: {len(tiers)}
REQUIRED_TIERS_IN_ORDER: {json.dumps(tiers)}

ALWAYS BUILD THE QUOTE:
Never answer that a quote cannot be made. Either ask one short question (ready=false, one question) when a single missing fact changes the whole scope, or build every option now (ready=true) with clearly stated assumptions.
missing_services is only for individual parts or services that truly need a price in that option. Never put a summary of the option's scope there (that is what bullets are for), never repeat something the option already prices with a service, item or direct price, and never list the same thing twice. Every missing_services entry must have a matching pricing_needed entry.
Anything you cannot match to a Service or Pricing Sheet item, and whose price the administrator has not typed in this conversation, still goes into the option: put it in that option's missing_services with its quantity and unit, and add one entry to pricing_needed with a realistic suggested material price per unit and labor hours per unit (typical Pennsylvania supply-house pricing) and a short reason. These are suggestions for the administrator to confirm; they are never added to the quote's totals by you.
Use direct_priced_items only when an administrator message in this conversation states that exact price; copy that message into source_message word for word.
"""
    schema = {
        "type": "object",
        "properties": {
            "ready": {"type": "boolean"},
            "message": {"type": "string"},
            "questions": {"type": "array", "maxItems": 1, "items": {"type": "string"}},
            "quote_title": {"type": "string"},
            "introduction": {"type": "string"},
            "mike_question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "string", "enum": tiers},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "bullets": {"type": "array", "minItems": 2, "items": {"type": "string"}},
                        "services": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"service_id": {"type": "integer"}, "multiplier": {"type": "number", "exclusiveMinimum": 0}},
                                "required": ["service_id", "multiplier"],
                                "additionalProperties": False,
                            },
                        },
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"price_item_id": {"type": "integer"}, "quantity": {"type": "number", "exclusiveMinimum": 0}},
                                "required": ["price_item_id", "quantity"],
                                "additionalProperties": False,
                            },
                        },
                        "direct_priced_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "quantity": {"type": "number", "exclusiveMinimum": 0},
                                    "unit": {"type": "string", "enum": ["ea", "ft", "roll", "box", "lb", "job"]},
                                    "material_unit_price": {"type": "number", "minimum": 0},
                                    "labor_hours_per_unit": {"type": "number", "minimum": 0},
                                    "create_in_price_sheet": {"type": "boolean"},
                                    "source_message": {"type": "string"},
                                },
                                "required": ["name", "quantity", "unit", "material_unit_price", "labor_hours_per_unit", "create_in_price_sheet", "source_message"],
                                "additionalProperties": False,
                            },
                        },
                        "missing_services": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "quantity": {"type": "number", "exclusiveMinimum": 0},
                                           "unit": {"type": "string", "enum": ["ea", "ft", "roll", "box", "lb", "job"]}},
                            "required": ["name", "quantity", "unit"], "additionalProperties": False}},
                    },
                    "required": ["tier", "title", "description", "bullets", "services", "items", "direct_priced_items", "missing_services"],
                    "additionalProperties": False,
                },
            },
            "pricing_needed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "unit": {"type": "string", "enum": ["ea", "ft", "roll", "box", "lb", "job"]},
                        "suggested_material_price": {"type": "number", "minimum": 0},
                        "suggested_labor_hours": {"type": "number", "minimum": 0},
                        "reason": {"type": "string"},
                    },
                    "required": ["name", "unit", "suggested_material_price", "suggested_labor_hours", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["ready", "message", "questions", "quote_title", "introduction", "mike_question", "options", "pricing_needed"],
        "additionalProperties": False,
    }
    client = OpenAI()
    response_args = {"model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"), "instructions": instructions, "text": {"format": {"type": "json_schema", "name": "powerhouse_quote_plan", "schema": schema, "strict": True}}}
    stopped_unconfirmed = False
    try:
        response = client.responses.create(input=conversation, **response_args)
    except Exception as error:  # noqa: BLE001
        too_big = any(word in str(error).lower() for word in ("context", "too long", "maximum", "tokens"))
        if not (unconfirmed_rows and too_big):
            raise
        # the safety stop: quoting must keep working, so Unconfirmed items are switched off and the quote is built without them
        row = db.session.get(CompanySetting, "caden_unconfirmed") or CompanySetting(key="caden_unconfirmed")
        row.value = "0"; db.session.add(row); db.session.commit()
        instructions = instructions.replace(json.dumps(price_catalog + unconfirmed_rows), json.dumps(price_catalog))
        response_args["instructions"] = instructions
        response = client.responses.create(input=conversation, **response_args)
        stopped_unconfirmed = True
    result = json.loads(response.output_text)
    if stopped_unconfirmed:
        result["message"] = (str(result.get("message") or "") + "\n\nNote: the quote was too big with Cooper's Unconfirmed items, so I built it with confirmed items only and switched Unconfirmed items off for quoting. You can turn them back on from the Pricing Sheet.").strip()
    if not result.get("ready") and not [q for q in result.get("questions", []) if str(q).strip()]:
        # "not ready" with nothing to ask is a dead end: build it with stated assumptions instead
        proceed = conversation + [{"role": "user", "content": "Build all the options now using clearly stated assumptions. Put anything unpriced in missing_services and pricing_needed with a suggested price."}]
        response = client.responses.create(input=proceed, **response_args)
        result = json.loads(response.output_text)
    mike_question = str(result.get("mike_question", "")).strip()
    if mike_question and mike:
        mike_answer = ask_mike_labor_question(mike, mike_question)
        consultation = conversation + [{"role":"user","content":f"MIKE LABOR CONSULTATION\nQuestion: {mike_question}\nMike's advisory answer: {mike_answer}\n\nNow finish the selected quote options. Keep stored Service and Pricing Sheet labor authoritative. If Mike identified labor that is not approved centrally, disclose the gap rather than silently changing deterministic quote math. Set mike_question to an empty string."}]
        response = client.responses.create(input=consultation, **response_args)
        result = json.loads(response.output_text)
    if result.get("ready"):
        try:
            validate_ai_option_progression(result.get("options", []), tiers)
        except ValueError as error:
            correction = conversation + [{"role": "user", "content": f"Your draft failed this required progression rule: {error} Rebuild all {len(tiers)} options. Every higher tier must add scope or substitute a clearly better-quality Service or Pricing Sheet item."}]
            response = client.responses.create(input=correction, **response_args)
            retry = json.loads(response.output_text)
            if retry.get("ready"):
                result = retry                 # still imperfect? the draft is built anyway and the problem is noted on it
    return result


def generate_inspection_tip(job):
    fallback = "Start with the requested work, then check service and panel capacity, required protection, grounding, nearby wiring condition, and any related areas the client may not realize are affected."
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        return fallback
    try:
        response = OpenAI().responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            instructions="You are Caden, Power House Electrical Solutions' onsite sales assistant. Give one concise, practical inspection tip for an electrician. Focus on safety, related systems, useful client questions, and scope dependencies. Do not diagnose unseen conditions, invent code violations, mention prices, or pressure a sale. Keep it under 90 words.",
            input=f"Job title: {job.title}\nClient request: {job.description}\nWork address: {job.job_address}",
        )
        return response.output_text.strip() or fallback
    except Exception:
        app.logger.exception("Inspection tip generation failed")
        return fallback


def validate_direct_priced_item(quote, selection):
    name = str(selection.get("name", "")).strip()
    source = " ".join(str(selection.get("source_message", "")).split())
    user_messages = [" ".join(message.content.split()) for message in quote.messages if message.role == "user"]
    if not name or not source or source not in user_messages:
        raise ValueError(f'Direct pricing for "{name or "unnamed item"}" was not tied to an exact user message.')
    price = float(selection.get("material_unit_price", -1))
    labor = float(selection.get("labor_hours_per_unit", 0))
    quantity = float(selection.get("quantity", 0))
    stated_numbers = [float(value.replace(",", "")) for value in re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", source)]
    if price < 0 or quantity <= 0 or not any(abs(number - price) < .0001 for number in stated_numbers):
        raise ValueError(f'Direct material price for "{name}" was not explicitly stated in the supporting user message.')
    if labor < 0 or (labor > 0 and not any(abs(number - labor) < .0001 for number in stated_numbers)):
        raise ValueError(f'Direct labor time for "{name}" was not explicitly stated in the supporting user message.')
    if selection.get("create_in_price_sheet"):
        source_key = canonical_item_name(source)
        requested_save = ("pricing sheet" in source_key or "price sheet" in source_key or "catalog" in source_key) and any(word in source_key.split() for word in {"add", "create", "save"})
        if not requested_save:
            raise ValueError(f'Creating "{name}" in the Pricing Sheet requires an explicit add, create, or save request.')
    return name, quantity, price, labor


def remember_direct_pricing(agent, name, unit, price, labor, source):
    if agent is None:
        return
    fingerprint = f"{canonical_item_name(name)}|{unit}|{price:.4f}|{labor:.4f}"
    existing = AgentKnowledge.query.filter_by(agent_id=agent.id, category="User-supplied pricing").all()
    if any(fingerprint in entry.content for entry in existing):
        return
    content = f"PRICING_MEMORY:{fingerprint}\nAdministrator supplied: {name} at ${price:.2f}/{unit} with {labor:g} labor hours per unit. Supporting quote message: {source}"
    db.session.add(AgentKnowledge(agent=agent, title=f"Direct price — {name} — {datetime.utcnow().strftime('%Y-%m-%d')}", category="User-supplied pricing", content=content, active=True))


def _missing_entry(value):
    """A missing item as (name, quantity, unit), from Caden's object or an older plain string."""
    if isinstance(value, dict):
        try:
            quantity = float(value.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        unit = value.get("unit") if value.get("unit") in {"ea", "ft", "roll", "box", "lb", "job"} else "ea"
        return str(value.get("name") or "").strip(), max(quantity, 0.01), unit
    return str(value or "").strip(), 1.0, "ea"


SCOPE_FILLER = {"supply", "install", "installation", "and", "of", "the", "a", "an", "new", "for", "with", "premium", "turnkey",
                "replace", "replacement", "upgrade", "professional", "custom", "complete", "full", "residential", "grade", "set", "kit"}


def _scope_words(name):
    words = re.findall(r"[a-z0-9]+", str(name or "").lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in SCOPE_FILLER}


def _same_scope(a, b):
    """Two line names that describe the same thing ("LED-compatible dimmer switch (kitchen)" and
    "Supply & install LED-compatible dimmer switches")."""
    if not a or not b:
        return False
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return len(small & large) / len(small) >= .75 and len(small) >= 2


def normalize_ai_options(option_data, tiers):
    """Caden's options in the required tier order, whatever came back. Returns (options, notes).
    A tier Caden left out gets a placeholder to finish; duplicate tiers are kept and noted."""
    notes = []
    by_tier, spare = {}, []
    for data in option_data or []:
        if not isinstance(data, dict):
            continue
        if data.get("tier") in tiers and data["tier"] not in by_tier:
            by_tier[data["tier"]] = data
        else:
            spare.append(data)
    options = []
    for tier in tiers:
        data = by_tier.get(tier) or (spare.pop(0) if spare else None)
        if data is None:
            data = {"title": f"{tier} option", "description": "Caden did not finish this option. Ask him to complete it, or edit it by hand.",
                    "bullets": [], "services": [], "items": [], "direct_priced_items": [],
                    "missing_services": [{"name": f"Finish the {tier} option", "quantity": 1, "unit": "job"}]}
            notes.append(f"The {tier} option was not finished; it is marked for you to complete.")
        options.append(dict(data, tier=tier))
    try:
        validate_ai_option_progression(options, tiers)
    except ValueError as error:
        notes.append(f"{error} You may want to adjust it before sending.")
    return options, notes


def apply_ai_quote_options(quote, option_data):
    """Save Caden's options on the draft. Never fails on content: anything that breaks a pricing rule becomes a
    line marked "pricing needed" (and a suggestion to confirm) instead of throwing the quote away.
    Returns (notes, suggestions): sentences for the office, and {name: Caden's suggested price} for unpriced lines."""
    option_data, notes = normalize_ai_options(option_data, quote_tiers(quote.option_count))
    service_lookup = {service.id: service for service in Service.query.all()}
    item_lookup = {item.id: item for item in PriceItem.query.filter_by(active=True).all()}
    caden = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden").first()
    created_catalog_items = {}
    suggestions = {}
    quote.options.clear()

    def pending(option, name, quantity=1.0, unit="ea", suggestion=None):
        name = re.sub(r"\s*\((?:direct[- ]?priced|estimated?|estimate only|pricing (?:pending|needed)|needs? pric(?:e|ing)|unpriced)\)", "", str(name or ""), flags=re.IGNORECASE).strip()
        option.lines.append(QuoteOptionService(service=None, service_name=name[:180] or "Item to price", example_quantity="Pricing pending",
                                               multiplier=quantity, unit_price=0, position=len(option.lines)))
        if suggestion:
            suggestions.setdefault(normalize_item_name(name), dict(suggestion, name=name, unit=unit))

    for position, data in enumerate(option_data):
        professional_bullets = [line for line in (professionalize_scope_bullet(bullet) for bullet in data.get("bullets", [])) if line]
        option = QuoteOption(tier=data["tier"], title=plain_terms(data.get("title") or f"{data['tier']} option"),
                             description=plain_terms(data.get("description") or ""), bullets_json=json.dumps(professional_bullets), position=position)
        for selection in data.get("services", []):
            service = service_lookup.get(selection.get("service_id"))
            try:
                multiplier = float(selection.get("multiplier", 0))
            except (TypeError, ValueError):
                multiplier = 0
            if service is None or multiplier <= 0:
                pending(option, f"Service #{selection.get('service_id')} (no longer in the library)", max(multiplier, 1))
                notes.append(f"{data['tier']}: a service Caden picked is not in the library; it is marked to price.")
                continue
            option.lines.append(QuoteOptionService(service=service, service_name=service.name, example_quantity=service.example_quantity, multiplier=multiplier, unit_price=service.example_total, material_unit_price=service.material_total, labor_hours_per_unit=service.estimated_labor_hours, labor_rate_snapshot=get_labor_rate(), position=len(option.lines)))
        for selection in data.get("items", []):
            price_item = item_lookup.get(selection.get("price_item_id"))
            try:
                quantity = float(selection.get("quantity", 0))
            except (TypeError, ValueError):
                quantity = 0
            if price_item is None or quantity <= 0:
                pending(option, f"Pricing Sheet item #{selection.get('price_item_id')} (not found)", max(quantity, 1))
                notes.append(f"{data['tier']}: a Pricing Sheet item Caden picked was not found; it is marked to price.")
                continue
            rate = get_labor_rate()
            option.lines.append(QuoteOptionService(service=None, price_item=price_item, service_name=price_item.name, example_quantity=f"1 {price_item.unit}", multiplier=quantity, unit_price=price_item.price + (price_item.labor_hours_per_unit * rate), material_unit_price=price_item.price, labor_hours_per_unit=price_item.labor_hours_per_unit, labor_rate_snapshot=rate, position=len(option.lines)))
        for selection in data.get("direct_priced_items", []):
            unit = selection.get("unit") if selection.get("unit") in {"ea", "ft", "roll", "box", "lb", "job"} else "ea"
            try:
                name, quantity, material_price, labor = validate_direct_priced_item(quote, selection)
            except (ValueError, TypeError):
                # a price nobody typed: never in the totals, but kept as Caden's suggestion to confirm
                name = str(selection.get("name") or "Item to price").strip()
                try:
                    quantity = max(float(selection.get("quantity") or 1), 0.01)
                    suggested = {"suggested_material_price": max(float(selection.get("material_unit_price") or 0), 0),
                                 "suggested_labor_hours": max(float(selection.get("labor_hours_per_unit") or 0), 0)}
                except (TypeError, ValueError):
                    quantity, suggested = 1.0, None
                if suggested is not None:
                    suggested["reason"] = "Caden's estimate; confirm before sending."
                pending(option, name, quantity, unit, suggested)
                continue
            source = " ".join(str(selection.get("source_message", "")).split())
            price_item = None
            if selection.get("create_in_price_sheet"):
                key = canonical_item_name(name)
                price_item = created_catalog_items.get(key) or find_duplicate_price_item(name)
                if price_item is None:
                    price_item = PriceItem(name=name, lookup_key=normalize_item_name(name), category="Quote supplied", unit=unit, price=material_price, labor_hours_per_unit=labor, notes="Created from an explicit Caden quote-chat instruction.", active=True)
                    db.session.add(price_item); db.session.flush()
                else:
                    price_item.active = True; price_item.price = material_price; price_item.unit = unit
                    if labor > 0: price_item.labor_hours_per_unit = labor
                created_catalog_items[key] = price_item
            remember_direct_pricing(caden, name, unit, material_price, labor, source)
            rate = get_labor_rate()
            option.lines.append(QuoteOptionService(service=None, price_item=price_item, service_name=price_item.name if price_item else name, example_quantity=f"1 {unit}", multiplier=quantity, unit_price=material_price + (labor * rate), material_unit_price=material_price, labor_hours_per_unit=labor, labor_rate_snapshot=rate, manual_priced=price_item is None, position=len(option.lines)))
        for value in data.get("missing_services", []):
            name, quantity, unit = _missing_entry(value)
            if not name:
                continue
            key = _scope_words(name)
            if any(_same_scope(key, _scope_words(line.service_name)) for line in option.lines):
                continue                       # a restatement of something this option already has
            pending(option, name, quantity, unit)
        if not option.lines:
            pending(option, "Service library match needed")
        quote.options.append(option)
    return list(dict.fromkeys(notes)), suggestions


def remember_pricing_suggestions(quote, ai_suggestions, line_suggestions):
    """Keep Caden's suggested prices on the draft, by item name, for the "Needs a price" panel."""
    saved = quote.pricing_needed
    for row in (ai_suggestions or []):
        if isinstance(row, dict) and str(row.get("name") or "").strip():
            saved[normalize_item_name(row["name"])] = {k: row.get(k) for k in ("name", "unit", "suggested_material_price", "suggested_labor_hours", "reason")}
    for key, row in (line_suggestions or {}).items():
        saved.setdefault(key, row)
    quote.pricing_needed_json = json.dumps(saved)


def quote_ready_summary(quote, notes=()):
    """What Caden says after building the options: what needs a price, and anything to look at."""
    rows = pricing_needed_rows(quote)
    parts = []
    if rows:
        names = ", ".join(row["name"] for row in rows[:6]) + (" and more" if len(rows) > 6 else "")
        parts.append(f"All {quote.option_count} options are built. {len(rows)} item{'s need' if len(rows) != 1 else ' needs'} a price before this quote is final: {names}. "
                     "They're at the top of the draft with my suggested prices; adjust them if needed and press Use these prices.")
    for note in notes:
        parts.append("• " + note)
    return "\n\n".join(parts)


def pricing_needed_rows(quote):
    """Every unpriced item on the draft, once, with Caden's suggestion when he made one."""
    saved = quote.pricing_needed
    rows = []
    for group in build_missing_quote_groups(quote):
        key = normalize_item_name(group["name"])
        suggestion = saved.get(key) or next((row for other, row in saved.items()
                                              if SequenceMatcher(None, other, key).ratio() >= .82), {})
        quantities = sorted({line.multiplier for line in group["lines"]})
        rows.append({"key": group["key"], "name": group["name"], "tiers": group["tiers"], "lines": len(group["lines"]),
                     "quantities": quantities, "quantity_text": " / ".join("%g" % q for q in quantities), "unit": suggestion.get("unit") or "ea",
                     "material": suggestion.get("suggested_material_price"), "labor": suggestion.get("suggested_labor_hours"),
                     "reason": suggestion.get("reason") or ""})
    rows.sort(key=lambda row: row["material"] is None)          # Caden's priced suggestions first
    return rows


def populate_client_from_form(client):
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    company_name = request.form.get("company_name", "").strip()
    if not (first_name and last_name) and not company_name:
        return "Enter a first and last name, or a company name."
    client.first_name = first_name or ("" if last_name else company_name)
    client.last_name = last_name
    client.company_name = company_name[:180]
    client.other_contacts = request.form.get("other_contacts", "").strip()
    client.tags = ", ".join(t.strip() for t in request.form.get("tags", "").split(",") if t.strip())[:255]
    client.lead_source = request.form.get("lead_source", "").strip()[:120]
    client.email = request.form.get("email", "").strip()
    client.phone = request.form.get("phone", "").strip()
    client.address_line1 = request.form.get("address_line1", "").strip()
    client.address_line2 = request.form.get("address_line2", "").strip()
    client.city = request.form.get("city", "").strip()
    client.state = request.form.get("state", "").strip() or "PA"
    client.postal_code = request.form.get("postal_code", "").strip()
    client.notes = request.form.get("notes", "").strip()
    return None


def parse_optional_datetime(value, label):
    if not value:
        return None, None
    try:
        return datetime.fromisoformat(value), None
    except ValueError:
        return None, f"{label} must be a valid date and time."


def populate_job_from_form(job):
    client_id = request.form.get("client_id", "")
    client = db.session.get(Client, int(client_id)) if client_id.isdigit() else None
    title = request.form.get("title", "").strip()
    if not client:
        return "Select a client."
    if not title:
        return "Job title is required."
    address_line1 = request.form.get("address_line1", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    postal_code = request.form.get("postal_code", "").strip()
    if not address_line1 or not city or not state or not postal_code:
        return "Street address, city, state, and ZIP code are required for automatic travel pricing."
    previous_address = job.job_address
    previous_override = job.drive_minutes_override
    override_value = request.form.get("drive_minutes_override", "").strip()
    try:
        drive_minutes_override = max(float(override_value), 0) if override_value else None
    except ValueError:
        return "Manual one-way travel time must be a number of minutes."
    review_at, error = parse_optional_datetime(request.form.get("review_at", ""), "Site review")
    if error:
        return error
    quote_at, error = parse_optional_datetime(request.form.get("quote_presentation_at", ""), "Quote presentation")
    if error:
        return error
    work_date_value = request.form.get("work_start_date", "")
    try:
        work_start_date = date.fromisoformat(work_date_value) if work_date_value else None
        duration_days = max(int(request.form.get("duration_days", "1") or 1), 1)
    except ValueError:
        return "Work date and duration must be valid."
    status = request.form.get("status", "new_request")
    if status not in JOB_STATUS_LABELS:
        return "Select a valid job status."
    assigned_id = request.form.get("assigned_user_id", "")
    assigned_user = db.session.get(User, int(assigned_id)) if assigned_id.isdigit() else None
    job.client = client
    job.title = title
    job.description = request.form.get("description", "").strip()
    job.address_line1 = address_line1
    job.address_line2 = request.form.get("address_line2", "").strip()
    job.city = city
    job.state = state
    job.postal_code = postal_code
    job.drive_minutes_override = drive_minutes_override
    if (previous_address and previous_address != job.job_address) or previous_override != drive_minutes_override:
        job.drive_minutes = None; job.drive_distance_miles = None; job.drive_checked_at = None; job.drive_error = ""
    job.status = status
    job.assigned_user = assigned_user
    job.review_at = review_at
    job.quote_presentation_at = quote_at
    job.work_start_date = work_start_date
    job.duration_days = duration_days
    job.internal_notes = request.form.get("internal_notes", "").strip()
    try:
        job.contract_amount = max(float(request.form.get("contract_amount", job.contract_amount or 0) or 0), 0)
        job.material_cost = max(float(request.form.get("material_cost", job.material_cost or 0) or 0), 0)
    except ValueError:
        return "Job value and material cost must be valid numbers."
    return None


def save_price_item_from_form(item=None):
    name = request.form.get("name", "").strip()
    if not name:
        return "Item name is required.", item
    lookup_key = normalize_item_name(name)
    supplier_names = request.form.getlist("supplier_name[]")
    supplier_numbers = request.form.getlist("supplier_part_number[]")
    supplier_parts = []
    seen_supplier_parts = set()
    for index, raw_number in enumerate(supplier_numbers):
        number = raw_number.strip()
        if not number:
            continue
        supplier = (supplier_names[index].strip() if index < len(supplier_names) else "") or "Other"
        key = (canonical_item_name(supplier), canonical_part_number(number))
        if key in seen_supplier_parts:
            continue
        seen_supplier_parts.add(key); supplier_parts.append((supplier, number))
    legacy_cooper = request.form.get("cooper_part_number", "").strip()
    if legacy_cooper and not any("cooper" in canonical_item_name(supplier) for supplier, _ in supplier_parts):
        supplier_parts.append(("Cooper Electric", legacy_cooper))
    part_number = next((number for supplier, number in supplier_parts if "cooper" in canonical_item_name(supplier)), "")
    # Renaming an existing row must only be blocked by a different active row.
    # Hidden/deleted historical rows are intentionally ignored during edits.
    duplicate = find_duplicate_price_item(
        name,
        part_number,
        exclude_id=item.id if item else None,
        include_inactive=item is None,
        supplier_parts=supplier_parts,
    )
    if duplicate and item is not None:
        return f'"{name}" matches existing item "{duplicate.name}". Edit that item instead.', item
    if duplicate and item is None and duplicate.active:
        return f'"{name}" already exists as "{duplicate.name}". The existing item was kept.', duplicate
    try:
        price = max(float(request.form.get("price", "0") or 0), 0)
        labor_hours = max(float(request.form.get("labor_hours_per_unit", "0") or 0), 0)
    except ValueError:
        return "Price and labor hours must be numbers.", item
    if item is not None:
        # Older cleanup operations can leave an inactive row holding the unique
        # lookup key for a name. Archive that hidden key so the visible row can
        # be renamed without resurrecting or overwriting historical data.
        archived_hidden_key = False
        for hidden in PriceItem.query.filter(PriceItem.id != item.id, PriceItem.lookup_key == lookup_key, PriceItem.active.is_(False)).all():
            hidden.lookup_key = f"archived-{hidden.id}-{lookup_key}"
            archived_hidden_key = True
        if archived_hidden_key:
            db.session.flush()
    item = item or duplicate or PriceItem()
    item.name = name
    item.lookup_key = lookup_key
    item.category = request.form.get("category", "").strip() or "General"
    item.unit = request.form.get("unit", "").strip() or "ea"
    item.price = price
    item.labor_hours_per_unit = labor_hours
    item.cooper_part_number = part_number
    item.notes = request.form.get("notes", "").strip()
    item.active = True
    # Reconcile supplier rows in place. Clearing and recreating an unchanged
    # part number can make SQLite insert the replacement before deleting the
    # original, which violates the supplier-part unique constraint.
    existing_rows = list(item.supplier_parts)
    unmatched_rows = list(existing_rows)
    unmatched_parts = []
    for supplier, number in supplier_parts:
        exact = next((row for row in unmatched_rows if canonical_item_name(row.supplier) == canonical_item_name(supplier) and canonical_part_number(row.part_number) == canonical_part_number(number)), None)
        if exact:
            exact.supplier = supplier
            exact.part_number = number
            unmatched_rows.remove(exact)
        else:
            unmatched_parts.append((supplier, number))
    # Reuse old rows for changed values before creating any new rows.
    for supplier, number in unmatched_parts:
        if unmatched_rows:
            row = unmatched_rows.pop(0)
            row.supplier = supplier
            row.part_number = number
        else:
            item.supplier_parts.append(SupplierPartNumber(supplier=supplier, part_number=number))
    for row in unmatched_rows:
        item.supplier_parts.remove(row)
    db.session.add(item)
    db.session.commit()
    return None, item


def populate_service_from_form(service):
    name = request.form.get("name", "").strip()
    if not name:
        return "Service name is required."
    duplicate_service = next((candidate for candidate in Service.query.all() if candidate.id != service.id and canonical_item_name(candidate.name) == canonical_item_name(name)), None)
    if duplicate_service:
        return f'A service named "{duplicate_service.name}" already exists. Edit or reuse that service instead.'
    service.name = name
    service.category = request.form.get("category", "").strip() or "General"
    service.description = request.form.get("description", "").strip()
    service.example_quantity = request.form.get("example_quantity", "").strip() or "1 job"
    try:
        service.estimated_labor_hours = max(float(request.form.get("estimated_labor_hours", "0") or 0), 0)
    except ValueError:
        return "Estimated labor time must be a number."

    drawing = request.files.get("drawing")
    if drawing and drawing.filename:
        extension = os.path.splitext(drawing.filename)[1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}:
            return "Drawing must be a PNG, JPG, WEBP, GIF, or PDF."
        upload_dir = os.path.join(app.static_folder, "uploads", "services")
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = secure_filename(drawing.filename)
        unique_name = f"{secrets.token_hex(8)}-{safe_name}"
        drawing.save(os.path.join(upload_dir, unique_name))
        service.drawing_filename = unique_name

    service.items.clear()
    item_names = request.form.getlist("item_name[]")
    quantities = request.form.getlist("item_quantity[]")
    units = request.form.getlist("item_unit[]")
    prices = request.form.getlist("item_price[]")
    cooper_part_numbers = request.form.getlist("item_cooper_part_number[]")
    notes = request.form.getlist("item_notes[]")
    price_item_ids = request.form.getlist("item_price_id[]")
    service_lines_by_price_item = {}
    for position, item_name in enumerate(item_names):
        item_name = item_name.strip()
        if not item_name:
            continue
        try:
            quantity = max(float(quantities[position] or 0), 0)
            price = max(float(prices[position] or 0), 0)
        except (ValueError, IndexError):
            return f'Quantity and price for "{item_name}" must be numbers.'
        unit = (units[position].strip() if position < len(units) else "ea") or "ea"
        cooper_part_number = cooper_part_numbers[position].strip() if position < len(cooper_part_numbers) else ""
        price_item = None
        if position < len(price_item_ids) and price_item_ids[position].isdigit():
            price_item = db.session.get(PriceItem, int(price_item_ids[position]))
            if price_item and not price_item.active:
                price_item = None
            elif price_item and canonical_item_name(price_item.name) != canonical_item_name(item_name):
                # The user renamed the Service row instead of selecting the
                # existing catalog suggestion, so this is a new catalog item.
                price_item = None
        if price_item is None:
            price_item = find_duplicate_price_item(item_name, cooper_part_number, include_inactive=True, supplier_parts=[("Cooper Electric", cooper_part_number)] if cooper_part_number else [])
        if price_item is None:
            # A renamed Service material is a new central catalog item. Never
            # inherit the previous row's price; require Pricing Sheet review.
            price_item = PriceItem(name=item_name, lookup_key=normalize_item_name(item_name), category="General", unit=unit, price=0, cooper_part_number=cooper_part_number)
            if cooper_part_number:
                price_item.supplier_parts.append(SupplierPartNumber(supplier="Cooper Electric", part_number=cooper_part_number))
            db.session.add(price_item)
        else:
            price_item.active = True
            price_item.price = price
            price_item.unit = unit
            price_item.cooper_part_number = cooper_part_number
            cooper_rows = [row for row in price_item.supplier_parts if "cooper" in canonical_item_name(row.supplier)]
            if cooper_part_number and cooper_rows:
                # Update the existing row in place. Deleting and recreating an
                # identical row can violate SQLite's unique constraint because
                # inserts may be flushed before pending deletes.
                keeper = next((row for row in cooper_rows if canonical_part_number(row.part_number) == canonical_part_number(cooper_part_number)), cooper_rows[0])
                keeper.supplier = "Cooper Electric"
                keeper.part_number = cooper_part_number
                for extra in cooper_rows:
                    if extra is not keeper:
                        price_item.supplier_parts.remove(extra)
            elif cooper_part_number:
                price_item.supplier_parts.append(SupplierPartNumber(supplier="Cooper Electric", part_number=cooper_part_number))
            else:
                for row in cooper_rows:
                    price_item.supplier_parts.remove(row)
        db.session.flush()
        if price_item.id in service_lines_by_price_item:
            existing_line = service_lines_by_price_item[price_item.id]
            existing_line.quantity += quantity
            extra_note = notes[position].strip() if position < len(notes) else ""
            if extra_note and extra_note not in existing_line.notes:
                existing_line.notes = "; ".join(filter(None, [existing_line.notes, extra_note]))
            continue
        line = ServiceItem(name=price_item.name, quantity=quantity, unit=price_item.unit, unit_price=price_item.price, notes=notes[position].strip() if position < len(notes) else "", position=position)
        line.pricing_link = ServiceItemPricingLink(price_item=price_item)
        service.items.append(line)
        db.session.add(line)
        service_lines_by_price_item[price_item.id] = line

    service.tools.clear()
    tool_names = request.form.getlist("tool_name[]")
    tool_ids = request.form.getlist("tool_id[]")
    tool_notes = request.form.getlist("tool_notes[]")
    critical_tools = set(request.form.getlist("tool_critical[]"))
    for position, tool_name in enumerate(tool_names):
        tool_name = tool_name.strip()
        if tool_name:
            catalog_tool = None
            if position < len(tool_ids) and tool_ids[position].isdigit():
                catalog_tool = db.session.get(ToolCatalog, int(tool_ids[position]))
                if catalog_tool and not catalog_tool.active:
                    catalog_tool = None
                elif catalog_tool and normalize_item_name(catalog_tool.name) != normalize_item_name(tool_name):
                    catalog_tool = None
            if catalog_tool is None:
                catalog_tool = ToolCatalog.query.filter_by(lookup_key=normalize_item_name(tool_name), active=True).first()
            if catalog_tool is None:
                catalog_tool = ToolCatalog(name=tool_name, lookup_key=normalize_item_name(tool_name), tool_type="Other", rental_cost=0)
                db.session.add(catalog_tool)
                db.session.flush()
            service.tools.append(ServiceTool(tool_id=catalog_tool.id, name=catalog_tool.name, tool_type=catalog_tool.tool_type, rental_cost=catalog_tool.rental_cost, notes=tool_notes[position].strip() if position < len(tool_notes) else "", is_critical=str(position) in critical_tools, position=position))
    return None


# ---------------- Power House Flow ----------------
# A flow chart of how the business runs, first contact to the last step, with branches.
# It reads and writes nothing but its own table. Drawn and edited by static/js/flow.js.

FLOW_KINDS = {"start", "step", "decision", "end"}
FLOW_AUTO = {"", "manual", "partial", "software"}
FLOW_MAX_NODES = 400
FLOW_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def flow_clean(data):
    """Keep only what the editor makes: steps and the arrows between them. Anything else is dropped."""
    nodes, seen = [], set()
    for raw in (data.get("nodes") if isinstance(data, dict) else None) or []:
        if not isinstance(raw, dict) or len(nodes) >= FLOW_MAX_NODES:
            continue
        node_id = str(raw.get("id") or "")
        if not FLOW_ID.match(node_id) or node_id in seen:
            continue
        seen.add(node_id)
        nodes.append({"id": node_id,
                      "title": str(raw.get("title") or "").strip()[:200] or "Untitled step",
                      "kind": raw.get("kind") if raw.get("kind") in FLOW_KINDS else "step",
                      "who": str(raw.get("who") or "").strip()[:80],
                      "auto": raw.get("auto") if raw.get("auto") in FLOW_AUTO else "",
                      "notes": str(raw.get("notes") or "")[:4000]})
    edges, pairs = [], set()
    for raw in (data.get("edges") if isinstance(data, dict) else None) or []:
        if not isinstance(raw, dict):
            continue
        a, b = str(raw.get("from") or ""), str(raw.get("to") or "")
        if a not in seen or b not in seen or a == b or (a, b) in pairs:
            continue
        pairs.add((a, b))
        edges.append({"from": a, "to": b, "label": str(raw.get("label") or "").strip()[:80]})
    return {"nodes": nodes, "edges": edges}


def flow_chart():
    chart = FlowChart.query.order_by(FlowChart.id).first()
    if chart is None:
        chart = FlowChart(name="Power House Flow")
        db.session.add(chart)
        db.session.commit()
    return chart


@app.route("/flow")
@login_required
def flow_page():
    chart = flow_chart()
    try:
        data = flow_clean(json.loads(chart.data_json)) if chart.data_json else {"nodes": [], "edges": []}
    except ValueError:
        data = {"nodes": [], "edges": []}
    return render_template("flow.html", chart=chart, data=data)


@app.route("/flow/save", methods=["POST"])
@login_required
def flow_save():
    token = session.get("csrf_token")
    sent = request.headers.get("X-CSRF-Token", "")
    if not token or not sent or not secrets.compare_digest(token, sent):
        return jsonify({"ok": False, "said": "Your session expired. Reload the page."}), 400
    body = request.get_json(silent=True) or {}
    chart = flow_chart()
    if body.get("version") != chart.version:
        who = chart.updated_by.username if chart.updated_by else "someone"
        return jsonify({"ok": False, "conflict": True,
                        "said": "%s changed the flow since you opened it. Reload to see their version." % who}), 409
    chart.data_json = json.dumps(flow_clean(body.get("data") or {}), separators=(",", ":"))
    chart.version += 1
    chart.updated_at = datetime.utcnow()
    chart.updated_by = current_user
    db.session.commit()
    return jsonify({"ok": True, "version": chart.version})


# ---------------- Jobber (Settings -> Jobber) ----------------
# "Pull from Jobber" reads clients, requests, quotes and jobs out of Jobber (read only, jobber.py) into a
# file under instance/jobber/, and shows what an import would do. "Import" backs up the database and then
# creates or updates clients and jobs here. Records are linked by their Jobber id, so pulling again never
# duplicates anything; a record edited here since the last pull only gets its blank fields filled.

import jobber as jb

JOBBER_DIR = os.path.join(basedir, "instance", "jobber")


def jobber_setting(key, default=""):
    row = db.session.get(CompanySetting, "jobber_" + key)
    return row.value if row and row.value is not None else default


def put_jobber_setting(key, value, commit=True):
    row = db.session.get(CompanySetting, "jobber_" + key) or CompanySetting(key="jobber_" + key)
    row.value = value if isinstance(value, str) else json.dumps(value, default=str)
    db.session.add(row)
    if commit:
        db.session.commit()


def jobber_secret(key):
    stored = jobber_setting(key)
    if not stored:
        return ""
    try:
        return supplier_cipher().decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def put_jobber_secret(key, value, commit=True):
    put_jobber_setting(key, supplier_cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else "", commit)


def jobber_json(key):
    try:
        value = json.loads(jobber_setting(key) or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def jobber_session():
    tokens = {}
    try:
        tokens = json.loads(jobber_secret("tokens") or "{}")
    except ValueError:
        pass
    return jb.Session(jobber_setting("client_id"), jobber_secret("client_secret"), tokens,
                      save=lambda fresh: put_jobber_secret("tokens", json.dumps(fresh)))


def jobber_connected():
    return bool(jobber_setting("client_id") and jobber_secret("tokens"))


def jobber_callback_url():
    return url_for("jobber_callback", _external=True, _scheme="https")


def jobber_pull_state():
    state = jobber_json("pull")
    if state.get("status") == "running":
        try:
            started = datetime.fromisoformat(state.get("started_at", ""))
        except ValueError:
            started = None
        if started is None or datetime.utcnow() - started > timedelta(minutes=30):
            state.update(status="failed", error="The pull stopped without finishing (the web worker restarted). Pull again.")
            put_jobber_setting("pull", state)
    return state


def jobber_pulled():
    """The newest pull's data, or None."""
    path = jobber_pull_state().get("file") or ""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _jobber_pull_worker(by):
    with app.app_context():
        state = {"status": "running", "started_at": datetime.utcnow().isoformat(timespec="seconds"), "by": by, "log": []}

        def say(line):
            state["log"] = (state["log"] + [line])[-12:]
            put_jobber_setting("pull", state)

        try:
            data = jb.pull(jobber_session(), say)
            os.makedirs(JOBBER_DIR, exist_ok=True)
            path = os.path.join(JOBBER_DIR, "pull-%s.json" % datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            rel.prune(JOBBER_DIR, "pull-*.json", 5)
            state.update(status="done", file=path, finished_at=datetime.utcnow().isoformat(timespec="seconds"),
                         counts={k: len(data.get(k) or []) for k in jb.ORDER}, account=(data.get("account") or {}).get("name", ""),
                         calls=data.get("calls", 0))
            say("Done: %s." % ", ".join("%d %s" % (len(data.get(k) or []), k) for k in jb.ORDER))
        except jb.JobberError as exc:
            db.session.rollback()
            state.update(status="failed", error=str(exc), finished_at=datetime.utcnow().isoformat(timespec="seconds"))
            put_jobber_setting("pull", state)
        except Exception as exc:  # noqa: BLE001 — a pull never takes the web worker down
            db.session.rollback()
            state.update(status="failed", error="Unexpected %s: %s" % (exc.__class__.__name__, exc),
                         finished_at=datetime.utcnow().isoformat(timespec="seconds"))
            put_jobber_setting("pull", state)


def _digits(text):
    return re.sub(r"\D", "", text or "")[-10:]


def _edited_here(row):
    """True when someone changed this record here after the last pull: then the pull only fills blanks."""
    return bool(row.jobber_synced_at and row.updated_at and row.updated_at > row.jobber_synced_at)


def jobber_plan(data):
    """What an import of `data` would do, without changing anything.
    Returns {"clients": [...], "jobs": [...], "counts": {...}}; each entry says new · link · update · fill."""
    linked = {c.jobber_id: c for c in Client.query.filter(Client.jobber_id != "").all()}
    unlinked = Client.query.filter(Client.jobber_id == "").all()
    by_email = {c.email.strip().lower(): c for c in unlinked if c.email.strip()}
    by_phone = {_digits(c.phone): c for c in unlinked if len(_digits(c.phone)) >= 7}
    by_name = {(c.first_name.strip().lower(), c.last_name.strip().lower()): c for c in unlinked}
    taken = set()
    clients = []
    for raw in data.get("clients") or []:
        fields = jb.client_fields(raw)
        row, how = linked.get(fields["jobber_id"]), ""
        if row is None:
            for key, table, label in ((fields["email"].lower(), by_email, "same email"), (_digits(fields["phone"]), by_phone, "same phone"),
                                      ((fields["first_name"].lower(), fields["last_name"].lower()), by_name, "same name")):
                candidate = table.get(key) if key and key != ("", "") else None
                if candidate is not None and candidate.id not in taken and (not isinstance(key, str) or len(key) >= 3):
                    row, how = candidate, label
                    break
        if row is not None:
            taken.add(row.id)
        action = "new" if row is None else ("link" if not row.jobber_id else ("fill" if _edited_here(row) else "update"))
        clients.append({"fields": fields, "raw": raw, "client": row, "action": action, "why": how})

    local_jobs = Job.query.filter(db.or_(Job.jobber_id != "", Job.jobber_quote_id != "", Job.jobber_request_id != "")).all()
    by_job = {j.jobber_id: j for j in local_jobs if j.jobber_id}
    by_quote = {j.jobber_quote_id: j for j in local_jobs if j.jobber_quote_id}
    by_request = {j.jobber_request_id: j for j in local_jobs if j.jobber_request_id}
    quotes = {q.get("id"): q for q in (data.get("quotes") or [])}
    requests_ = {r.get("id"): r for r in (data.get("requests") or [])}
    jobs_raw = data.get("jobs") or []
    quotes_with_jobs = {(j.get("quote") or {}).get("id") for j in jobs_raw}
    requests_onward = {(q.get("request") or {}).get("id") for q in quotes.values()} | {(j.get("request") or {}).get("id") for j in jobs_raw}
    entries = []
    for raw in data.get("requests") or []:
        fields = jb.request_fields(raw)
        if fields["skip"] or fields["jobber_id"] in requests_onward:
            continue
        entries.append(("request", fields, {"request": raw}, by_request.get(fields["jobber_id"])))
    for raw in data.get("quotes") or []:
        fields = jb.quote_fields(raw)
        if fields["jobber_id"] in quotes_with_jobs or fields["quote_status"] == "converted":
            continue
        row = by_quote.get(fields["jobber_id"]) or by_request.get(fields["request_jobber_id"] or "-")
        entries.append(("quote", fields, {"quote": raw, "request": requests_.get(fields["request_jobber_id"])}, row))
    for raw in jobs_raw:
        fields = jb.job_fields(raw)
        row = by_job.get(fields["jobber_id"]) or by_quote.get(fields["quote_jobber_id"] or "-") or by_request.get(fields["request_jobber_id"] or "-")
        entries.append(("job", fields, {"job": raw, "quote": quotes.get(fields["quote_jobber_id"]), "request": requests_.get(fields["request_jobber_id"])}, row))
    jobs = []
    for kind, fields, raw, row in entries:
        action = "new" if row is None else ("fill" if _edited_here(row) else "update")
        jobs.append({"kind": kind, "fields": fields, "raw": raw, "job": row, "action": action})
    counts = {"clients_" + a: sum(1 for c in clients if c["action"] == a) for a in ("new", "link", "update", "fill")}
    counts.update({"jobs_" + a: sum(1 for j in jobs if j["action"] == a) for a in ("new", "update", "fill")})
    counts.update({"from_" + k: sum(1 for j in jobs if j["kind"] == k) for k in ("request", "quote", "job")})
    return {"clients": clients, "jobs": jobs, "counts": counts}


CLIENT_FIELDS = ("first_name", "last_name", "company_name", "email", "phone", "address_line1", "address_line2", "city", "state",
                 "postal_code", "other_contacts", "tags", "lead_source")


def _put(row, field, value, fill_only):
    if value is None or value == "":
        return
    if fill_only and getattr(row, field) not in (None, "", 0, 0.0):
        return
    setattr(row, field, value)


def jobber_apply(data):
    """Import `data`: create or update clients and jobs. Returns the plan's counts."""
    plan = jobber_plan(data)
    now = datetime.utcnow()
    client_for = {}
    for entry in plan["clients"]:
        fields, row = entry["fields"], entry["client"]
        fill_only = entry["action"] in ("fill", "link")       # a client matched here keeps what it has; blanks are filled
        if row is None:
            row = Client(first_name=fields["first_name"], last_name=fields["last_name"], state=fields["state"] or "PA",
                         created_at=fields["created_at"] or now)
            db.session.add(row)
            fill_only = False
        for field in CLIENT_FIELDS:
            _put(row, field, fields[field], fill_only)
        row.jobber_id, row.jobber_url = fields["jobber_id"], fields["jobber_url"] or row.jobber_url
        row.jobber_raw = json.dumps(entry["raw"])
        known = {p.jobber_id for p in row.properties if p.jobber_id}
        for prop in fields["properties"]:
            same_as_primary = (prop["address_line1"] or "").lower() == (row.address_line1 or "").lower()
            if prop["jobber_id"] in known or same_as_primary or not prop["address_line1"]:
                continue
            row.properties.append(ClientProperty(**prop))
        row.jobber_synced_at = now
        client_for[fields["jobber_id"]] = row
    db.session.flush()

    touched = []
    for entry in plan["jobs"]:
        fields, row = entry["fields"], entry["job"]
        client = client_for.get(fields.get("client_jobber_id"))
        if client is None and row is None:
            continue                                   # a record whose client did not come over: skipped
        fill_only = entry["action"] == "fill"
        if row is None:
            row = Job(client=client, title=fields["title"], job_number=next_job_number(), source="jobber",
                      created_at=fields.get("created_at") or now, state="")
            db.session.add(row)
            fill_only = False
        for field in ("title", "description", "address_line1", "address_line2", "city", "state", "postal_code"):
            _put(row, field, fields.get(field), fill_only)
        if not row.address_line1 and client is not None:
            for field in ("address_line1", "address_line2", "city", "state", "postal_code"):
                setattr(row, field, getattr(client, field) or getattr(row, field))
        if not fill_only:
            row.status = fields["status"]
            if entry["kind"] == "job":
                row.work_start_date = fields["work_start_date"] or row.work_start_date
                row.duration_days = fields["duration_days"] or row.duration_days
            if entry["kind"] == "request" and fields.get("review_at"):
                row.review_at = fields["review_at"]
        if entry["kind"] == "job":
            row.jobber_id, row.jobber_number = fields["jobber_id"], fields["jobber_number"]
            row.jobber_url = fields["jobber_url"] or row.jobber_url
            if fields["contract_amount"] is not None:
                _put(row, "contract_amount", fields["contract_amount"], fill_only)
            quote = jb.quote_fields(entry["raw"]["quote"]) if entry["raw"].get("quote") else None
            if fields["quote_jobber_id"]:
                row.jobber_quote_id = fields["quote_jobber_id"]
        else:
            quote = fields if entry["kind"] == "quote" else None
            if entry["kind"] == "request" or (quote and not row.jobber_url):
                row.jobber_url = fields["jobber_url"] or row.jobber_url
        if quote:
            row.jobber_quote_id = quote["jobber_id"]
            row.jobber_quote_number = quote["number"]
            row.jobber_quote_status = quote["quote_status"]
            row.jobber_quote_total = quote["total"]
        request_raw = entry["raw"].get("request")
        if request_raw:
            row.jobber_request_id = request_raw.get("id") or row.jobber_request_id
        if entry["kind"] == "job":
            detail = {"job": dict((k, fields[k]) for k in ("jobber_number", "salesperson", "job_type", "line_items", "visits", "invoices"))}
        else:
            detail = {}
        if quote:
            detail["quote"] = {k: quote[k] for k in ("number", "quote_status", "total", "salesperson", "line_items", "jobber_url")}
        detail["raw"] = entry["raw"]
        row.jobber_raw = json.dumps(detail, default=str)
        row.jobber_synced_at = now
        touched.append(row)
    db.session.commit()
    # stamp again after the save, so nothing just imported counts as "edited here" next time
    stamp = datetime.utcnow()
    for row in list(client_for.values()) + touched:
        row.jobber_synced_at = row.updated_at = stamp         # set together: any edit after this counts as "edited here"
    db.session.commit()
    return plan["counts"]


@app.route("/settings/jobber")
@login_required
def jobber_page():
    state = jobber_pull_state()
    data = jobber_pulled() if state.get("status") == "done" else None
    plan = jobber_plan(data) if data else None
    return render_template("jobber.html", client_id=jobber_setting("client_id"), has_secret=bool(jobber_secret("client_secret")),
                           connected=jobber_connected(), account=jobber_json("account"), callback_url=jobber_callback_url(),
                           state=state, plan=plan, data=data, last_import=jobber_json("last_import"), status_labels=JOB_STATUS_LABELS,
                           missing=(data or {}).get("missing") or [])


@app.route("/settings/jobber/keys", methods=["POST"])
@login_required
def jobber_keys():
    check_csrf()
    client_id = "".join(request.form.get("client_id", "").split())
    secret = "".join(request.form.get("client_secret", "").split())
    if not client_id or not (secret or jobber_secret("client_secret")):
        flash("Paste both the Client ID and the Client Secret from your app in Jobber's Developer Center.", "error")
        return redirect(url_for("jobber_page"))
    if client_id != jobber_setting("client_id"):
        put_jobber_secret("tokens", "", commit=False)          # a different app: connect again
    put_jobber_setting("client_id", client_id, commit=False)
    if secret:
        put_jobber_secret("client_secret", secret, commit=False)
    db.session.commit()
    return redirect(url_for("jobber_connect"))


@app.route("/settings/jobber/connect")
@login_required
def jobber_connect():
    if not jobber_setting("client_id"):
        flash("Save the Client ID and Secret first.", "error")
        return redirect(url_for("jobber_page"))
    session["jobber_state"] = secrets.token_urlsafe(24)
    return redirect(jb.authorize_url(jobber_setting("client_id"), jobber_callback_url(), session["jobber_state"]))


@app.route("/settings/jobber/callback")
@login_required
def jobber_callback():
    expected = session.pop("jobber_state", "")
    if request.args.get("error"):
        flash("Jobber did not connect: %s" % (request.args.get("error_description") or request.args.get("error")), "error")
        return redirect(url_for("jobber_page"))
    if not expected or not secrets.compare_digest(expected, request.args.get("state", "")):
        flash("That sign-in link was stale. Press Connect with Jobber again.", "error")
        return redirect(url_for("jobber_page"))
    try:
        tokens = jb.exchange(jobber_setting("client_id"), jobber_secret("client_secret"), request.args.get("code", ""), jobber_callback_url())
        put_jobber_secret("tokens", json.dumps(tokens))
        sess = jobber_session()
        account = (sess.gql("{ account { id name } }") or {}).get("account") or {}
        put_jobber_setting("account", {"name": account.get("name", ""), "connected_at": datetime.utcnow().isoformat(timespec="seconds"),
                                       "by": current_user.username})
        flash("Connected to Jobber%s." % ((" (" + account["name"] + ")") if account.get("name") else ""), "success")
    except jb.JobberError as exc:
        flash(str(exc), "error")
    return redirect(url_for("jobber_page"))


@app.route("/settings/jobber/disconnect", methods=["POST"])
@login_required
def jobber_disconnect():
    check_csrf()
    put_jobber_secret("tokens", "", commit=False)
    put_jobber_setting("account", "{}", commit=False)
    db.session.commit()
    flash("Jobber disconnected. Everything already imported stays.", "success")
    return redirect(url_for("jobber_page"))


@app.route("/settings/jobber/pull", methods=["POST"])
@login_required
def jobber_pull():
    check_csrf()
    if not jobber_connected():
        flash("Connect Jobber first: it takes a Client ID and Secret from Jobber's Developer Center.", "error")
        return redirect(url_for("jobber_page"))
    if jobber_pull_state().get("status") == "running":
        flash("A pull is already running.", "error")
        return redirect(url_for("jobber_page"))
    put_jobber_setting("pull", {"status": "running", "started_at": datetime.utcnow().isoformat(timespec="seconds"),
                                "by": current_user.username, "log": ["Starting…"]})
    threading.Thread(target=_jobber_pull_worker, args=(current_user.username,), daemon=True).start()
    return redirect(url_for("jobber_page"))


@app.route("/settings/jobber/status")
@login_required
def jobber_status():
    state = jobber_pull_state()
    return jsonify({"status": state.get("status", ""), "log": state.get("log", [])[-6:], "error": state.get("error", "")})


@app.route("/settings/jobber/import", methods=["POST"])
@login_required
def jobber_import():
    check_csrf()
    data = jobber_pulled()
    if data is None:
        flash("Pull from Jobber first.", "error")
        return redirect(url_for("jobber_page"))
    backup = rel.backup_database(os.path.join(basedir, "instance", "app.db"), RELEASE_BACKUPS, "before-jobber-import")
    try:
        counts = jobber_apply(data)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        flash("The import stopped and nothing was saved (%s: %s)." % (exc.__class__.__name__, exc), "error")
        return redirect(url_for("jobber_page"))
    put_jobber_setting("last_import", {"at": datetime.utcnow().isoformat(timespec="seconds"), "by": current_user.username,
                                       "counts": counts, "backup": os.path.basename(backup) if backup else ""})
    flash("Imported from Jobber: %d new clients, %d new jobs, %d updated." % (
        counts["clients_new"], counts["jobs_new"], counts["clients_update"] + counts["clients_link"] + counts["clients_fill"] +
        counts["jobs_update"] + counts["jobs_fill"]), "success")
    return redirect(url_for("jobber_page"))


# ---------------- Generators ----------------
import generators as gen


@app.route("/generators/sizing", methods=["GET", "POST"])
@login_required
def generator_sizing():
    values = request.form if request.method == "POST" else request.args
    result = gen.size(values) if values.get("calculate") or request.method == "POST" else None
    if request.method == "POST":
        check_csrf()
        job = db.session.get(Job, request.form.get("job_id", type=int) or 0)
        if job is None:
            flash("Choose the job to save this sizing to.", "error")
        else:
            model = result["model"]["model"] if result["model"] else ""
            summary = (f"Needs {result['required_kw']:g} kW (running {result['running_kw']:g} kW, starting {result['starting_kw']:g} kW). "
                       + (f"Recommended: {result['model']['description']} ({model}, {result['model']['rated_kw']:g} kW on "
                          f"{'propane' if result['inputs']['fuel'] == 'lp' else 'natural gas'})." if model else "Larger than the air-cooled range.")
                       + (f" With load management: {result['with_load_management']['required_kw']:g} kW." if result["with_load_management"]["shed"] else ""))
            db.session.add(GeneratorSizing(job=job, inputs_json=json.dumps(result["inputs"]), required_kw=result["required_kw"], model=model,
                                           summary=summary, created_by=current_user))
            db.session.commit()
            flash(f"Saved the sizing to job #{job.job_number} {job.title}.", "success")
            return redirect(url_for("job_detail", job_id=job.id) + "#generator-sizing")
    jobs = Job.query.filter(~Job.status.in_(["completed", "declined"])).order_by(Job.updated_at.desc()).limit(200).all()
    defaults = gen.read_inputs(values)
    return render_template("generator_sizing.html", result=result, fields=gen.FIELDS, choices=gen.CHOICES, values=defaults,
                           models=gen.KOHLER_MODELS, jobs=jobs, selected_job=request.args.get("job_id", type=int))


# ---------------- Generators -> Upgrading ----------------
# Old unit out, new unit in: the wiring and gas each one needs (generators.upgrade, pure), whether what is there can stay,
# a priced estimate from the Pricing Sheet and the labor rate, questions to Caleb with the upgrade attached, and the whole
# brief handed to Caden to build the quote on a job.
UPGRADE_PRICE_FIELDS = {
    # name: (label, default); a blank price is filled from the Pricing Sheet when an item matches
    "unit_price": ("New generator (each)", None),
    "switch_price": ("Transfer switch (each)", None),
    "pad_price": ("Generator pad (each)", None),
    "wire_price": ("Feeder wire ($/ft per conductor)", None),
    "ground_price": ("Ground wire ($/ft)", None),
    "conduit_price": ("Conduit ($/ft)", None),
    "pipe_price": ("Gas pipe ($/ft)", None),
    "disposal": ("Haul away and disposal ($)", 0),
    "permit": ("Permits ($)", 0),
    "remove_hours": ("Rip out the old unit (hours)", 3),
    "install_hours": ("Set and connect the new unit (hours)", 6),
    "switch_hours": ("Swap the transfer switch (hours)", 4),
    "run_hours": ("Wire and gas runs (hours, blank = worked out)", None),
}
UPGRADE_YES_NO = {"replace_wire": "Replace the wiring", "replace_pipe": "Replace the gas line", "replace_switch": "Replace the transfer switch", "new_pad": "New pad"}


def _price_number(values, name):
    raw = str(values.get(name, "")).replace("$", "").replace(",", "").strip()
    try:
        return max(float(raw), 0.0) if raw else None
    except ValueError:
        return None


def _size_pattern(size):
    body = re.escape(size).replace(r"\-", "[- ]")
    return rf"(?<![\d/-]){body}(?![\d/-])"


def match_price_item(kind, size="", material="", conduit=""):
    """The Pricing Sheet item for a part of the upgrade, or None. Read only; Unconfirmed items count only when quoting uses them."""
    query = PriceItem.query.filter(PriceItem.active.is_(True))
    if not caden_uses_unconfirmed():
        query = query.filter(PriceItem.category != UNCONFIRMED)
    if kind == "generator":
        codes = [code for code in re.findall(r"\b(\d{4}|\d{2}RCL?A)\b", size)]
        if not codes:
            return None
        rows = query.filter(db.or_(*[PriceItem.name.ilike(f"%{c}%") for c in codes], *[PriceItem.cooper_part_number.ilike(f"%{c}%") for c in codes])).limit(50).all()
        return next(iter(sorted(rows, key=lambda r: r.price <= 0)), None)
    words = {"wire": ["awg", "thhn", "thwn", "xhhw", "wire", "#", "cu", "alum", "str"], "pipe": ["pipe", "black", "csst", "gas"],
             "conduit": [(conduit.split() or ["pvc"])[0].lower()]}[kind]
    rows = query.filter(db.or_(*[PriceItem.name.ilike(f"%{w}%") for w in words])).limit(800).all()
    size_re = re.compile(_size_pattern(size), re.IGNORECASE)
    picks = []
    for row in rows:
        name = row.name.lower()
        if not size_re.search(row.name) or row.unit not in ("ft", "roll"):
            continue
        if kind == "wire":
            aluminum = "alum" in name or re.search(r"\bal\b", name)
            if (material == "aluminum") != bool(aluminum) or not re.search(r"(?:#\s?|\b)" + re.escape(size) + r"(?:\s?(?:awg|ga|thhn|thwn|xhhw|cu|al)\b|\b)", name):
                continue
        if kind == "pipe" and any(w in name for w in ("nipple", "elbow", "tee", "coupling", "union", "cap", "valve", "connector", "fitting")):
            continue
        picks.append(row)
    picks.sort(key=lambda r: (r.unit != "ft", r.price <= 0))
    return picks[0] if picks else None


def upgrade_estimate(result, values):
    """Priced lines for the upgrade: typed prices first, else the Pricing Sheet's, else listed as needing a price."""
    i, new, wire, gas_new = result["inputs"], result["new"], result["wire_new"], result["gas_new"]
    prices = {name: _price_number(values, name) for name in UPGRADE_PRICE_FIELDS}
    hours = {name: (prices[name] if prices[name] is not None else UPGRADE_PRICE_FIELDS[name][1]) for name in ("remove_hours", "install_hours", "switch_hours")}
    chosen = {}
    for name, default in (("replace_wire", result["needs_new_wire"]), ("replace_pipe", result["needs_new_pipe"]),
                          ("replace_switch", result["needs_new_switch"]), ("new_pad", result["new"]["lb"] - result["old"]["lb"] > 100 or result["new"]["cooling"] != result["old"]["cooling"])):
        chosen[name] = values.get(name) == "yes" if values.get(name) in ("yes", "no") else bool(default)
    found = {}

    def price_of(field, kind, **match):
        if prices[field] is not None:
            return prices[field], "typed"
        item = match_price_item(kind, **match)
        if item is not None and item.price > 0:
            found[field] = item
            return float(item.price), item.name
        return 0.0, ""

    lines = []

    def add(label, qty, unit, price, labor, source):
        lines.append({"label": label, "qty": qty, "unit": unit, "price": price, "hours": labor, "source": source,
                      "total": round(qty * price, 2), "missing": price <= 0 and source != "none"})

    old = result["old"]
    add(f"Rip out and haul away the existing {old['brand']} {old['model']}", 1, "job", prices["disposal"] or 0.0, hours["remove_hours"], "none")
    price, source = price_of("unit_price", "generator", size=new["model"])
    add(f"{new['brand']} {new['model']}, set and connected", 1, "ea", price, hours["install_hours"], source)
    if chosen["new_pad"]:
        price, source = (prices["pad_price"], "typed") if prices["pad_price"] is not None else (0.0, "")
        add("New generator pad", 1, "ea", price, 1.0, source)
    if chosen["replace_switch"]:
        price, source = (prices["switch_price"], "typed") if prices["switch_price"] is not None else (0.0, "")
        add(f"New {new['brand']} automatic transfer switch", 1, "ea", price, hours["switch_hours"], source)
    run_hours = 0.0
    if chosen["replace_wire"]:
        feet = i["wire_feet"]
        price, source = price_of("wire_price", "wire", size=wire["size"], material=wire["material"])
        add(f"{wire['size']} AWG {wire['material']} feeder (3 conductors)", round(feet * 3), "ft", price, 0, source)
        price, source = price_of("ground_price", "wire", size=wire["ground"], material=wire["material"])
        add(f"{wire['ground']} AWG ground", round(feet), "ft", price, 0, source)
        price, source = price_of("conduit_price", "conduit", size=wire["conduit"], conduit=wire["conduit_kind"])
        add(f"{wire['conduit']} in {wire['conduit_kind']} conduit", round(feet), "ft", price, 0, source)
        run_hours += 1.5 + feet / 30
    if chosen["replace_pipe"] and gas_new["size"]:
        price, source = price_of("pipe_price", "pipe", size=gas_new["size"])
        add(f"{gas_new['size']} in gas pipe ({'propane' if i['fuel'] == 'lp' else 'natural gas'})", round(i["gas_feet"]), "ft", price, 0, source)
        run_hours += 1.5 + i["gas_feet"] / 25
    run_hours = prices["run_hours"] if prices["run_hours"] is not None else round(run_hours, 1)
    if run_hours:
        add("Wire and gas runs (labor)", 1, "job", 0.0, run_hours, "none")
    if prices["permit"]:
        add("Permits", 1, "job", prices["permit"], 0, "typed")
    rate = get_labor_rate()
    material = round(sum(line["total"] for line in lines), 2)
    labor_hours = round(sum(line["hours"] for line in lines), 1)
    labor = round(labor_hours * rate, 2)
    return {"lines": lines, "material": material, "labor_hours": labor_hours, "labor_rate": rate, "labor": labor,
            "total": round(material + labor, 2), "missing": [line["label"] for line in lines if line["missing"]],
            "chosen": chosen, "found": found, "prices": prices, "hours": hours, "run_hours": run_hours}


UPGRADE_STATE = (list(gen.UPGRADE_FIELDS) + ["old", "new"] + [f"{side}_{suffix}" for side in ("old", "new") for suffix in gen.OTHER_FIELDS]
                 + list(gen.UPGRADE_CHOICES) + list(UPGRADE_PRICE_FIELDS) + list(UPGRADE_YES_NO))


def upgrade_state(values):
    """The page's inputs, to carry in hidden fields and links (nothing else)."""
    return {name: values.get(name) for name in UPGRADE_STATE if values.get(name) not in (None, "")}


@app.route("/generators/upgrade")
@login_required
def generator_upgrade():
    values = request.args
    result = gen.upgrade(values) if values.get("calculate") else None
    estimate = upgrade_estimate(result, values) if result and current_user.can("view_quoting") else None
    state = upgrade_state(values)
    answer = None
    asked = request.args.get("asked", type=int)
    if asked:
        row = db.session.get(CalebMessage, asked)
        answer = row if row and row.user_id == current_user.id and row.role == "assistant" else None
    groups = {}
    for row in gen.UPGRADE_MODELS:
        groups.setdefault(row["brand"], []).append(row)
    jobs = Job.query.filter(~Job.status.in_(["completed", "declined"])).order_by(Job.updated_at.desc()).limit(200).all() if current_user.can("view_jobs") else []
    return render_template("generator_upgrade.html", result=result, estimate=estimate, inputs=gen.read_upgrade(values), groups=groups,
                           fields=gen.UPGRADE_FIELDS, choices=gen.UPGRADE_CHOICES, price_fields=UPGRADE_PRICE_FIELDS, other_fields=gen.OTHER_FIELDS, yes_no=UPGRADE_YES_NO,
                           state=state, answer=answer, jobs=jobs, selected_job=request.args.get("job_id", type=int),
                           brief=gen.upgrade_brief(result, estimate) if result else "", caleb_ready=bool(OpenAI is not None and os.environ.get("OPENAI_API_KEY")))


def _upgrade_summary(result, estimate):
    old, new = result["old"], result["new"]
    words = (f"{old['brand']} {old['model']} to {new['brand']} {new['model']}: {result['wire_new']['size']} AWG {result['wire_new']['material']}, "
             f"{result['gas_new']['size'] or 'over 2'} in gas ({'LP' if result['inputs']['fuel'] == 'lp' else 'NG'}).")
    if estimate:
        words += f" Estimate ${estimate['total']:,.2f}" + (" (some prices missing)" if estimate["missing"] else "") + "."
    return words


@app.route("/generators/upgrade/save", methods=["POST"])
@login_required
def generator_upgrade_save():
    check_csrf()
    job = db.session.get(Job, request.form.get("job_id", type=int) or 0)
    state = upgrade_state(request.form)
    if job is None or not current_user.can("view_jobs"):
        flash("Choose the job to save this upgrade to.", "error")
        return redirect(url_for("generator_upgrade", calculate=1, **state))
    result = gen.upgrade(request.form)
    estimate = upgrade_estimate(result, request.form) if current_user.can("view_quoting") else None
    db.session.add(GeneratorUpgrade(job=job, inputs_json=json.dumps(state), summary=_upgrade_summary(result, estimate),
                                    total=estimate["total"] if estimate else None, created_by=current_user))
    db.session.commit()
    flash(f"Saved the upgrade to job #{job.job_number} {job.title}.", "success")
    return redirect(url_for("job_detail", job_id=job.id) + "#generator-upgrade")


@app.route("/generators/upgrade/quote", methods=["POST"])
@login_required
def generator_upgrade_quote():
    """Save the upgrade to the job and hand Caden the brief: the quote assistant receives it as the office's message."""
    check_csrf()
    job = db.session.get(Job, request.form.get("job_id", type=int) or 0)
    if job is None or not current_user.can("view_quoting"):
        flash("Choose the job to quote this upgrade on.", "error")
        return redirect(url_for("generator_upgrade", calculate=1, **upgrade_state(request.form)))
    result = gen.upgrade(request.form)
    estimate = upgrade_estimate(result, request.form)
    db.session.add(GeneratorUpgrade(job=job, inputs_json=json.dumps(upgrade_state(request.form)), summary=_upgrade_summary(result, estimate),
                                    total=estimate["total"], created_by=current_user))
    db.session.commit()
    message = ("Quote this generator upgrade. Price the generator, transfer switch, pad, wire, conduit and gas pipe from the Pricing Sheet "
               "and include the time to rip out the old unit.\n\n" + gen.upgrade_brief(result, estimate))
    quote = draft_quote_for(job)
    try:
        refresh_quote_travel(quote)
        db.session.commit()
    except Exception:  # noqa: BLE001  (travel is added later on the quote page; the upgrade still goes to Caden)
        db.session.commit()
    send_quote_message(job, quote, message)            # exactly as if it were typed on the quote page
    return redirect(url_for("quote_assistant", job_id=job.id))


@app.route("/generators/upgrade/ask", methods=["POST"])
@login_required
def generator_upgrade_ask():
    check_csrf()
    state = upgrade_state(request.form)
    question = request.form.get("question", "").strip()[:2000]
    if not question:
        return redirect(url_for("generator_upgrade", calculate=1, **state) + "#ask")
    result = gen.upgrade(request.form)
    label = f"{result['old']['brand']} {result['old']['model']} to {result['new']['brand']} {result['new']['model']}"
    db.session.add(CalebMessage(user_id=current_user.id, role="user", content=f"{question}\n(About the generator upgrade: {label})")); db.session.commit()
    agent = caleb_agent()
    looked = []
    if agent is None or not agent.active or OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        answer = "I'm not connected right now, so call Caleb for this one. Your question was saved."
    else:
        try:
            answer, looked = ask_caleb(current_user, [{"role": "user", "content": "About this generator upgrade (worked out by the Upgrading page):\n"
                                                       + gen.upgrade_brief(result) + "\n\nQuestion: " + question}])
            answer = answer or "I couldn't put an answer together. Try asking another way, or call Caleb."
        except Exception as error:  # noqa: BLE001
            app.logger.exception("Caleb upgrade question failed")
            answer = f"I couldn't answer that just now. {describe_ai_error(error)}"
    row = CalebMessage(user_id=current_user.id, role="assistant", content=answer, looked_up="\n".join(looked))
    db.session.add(row); db.session.commit()
    return redirect(url_for("generator_upgrade", calculate=1, asked=row.id, **state) + "#ask")


# ---------------- Ask Caleb ----------------
# The team's first stop for help, before calling Caleb himself: electrical work and the code Pennsylvania enforces
# (2020 NEC), Kohler generators, and everything in this software about clients, jobs, quotes, services and prices.
# He reads the records through the tools below, only what the person asking may see (their role's permissions),
# and never changes anything. Admins teach him more under Settings -> AI agents -> Caleb (knowledge and reference files).

CALEB_IDENTITY = ("You are Caleb, Power House Electrical Solutions' in-house electrical expert and helper. Employees ask you first, "
                  "before calling Caleb (the owner) on the phone. Be practical, safe and plain-spoken, like a master electrician "
                  "talking to his own crew and office staff.")
CALEB_PROMPT = """Answer like a master electrician who also knows this company's software inside out.
- Electrical work and code: Pennsylvania enforces the 2020 National Electrical Code (NFPA 70) through the Uniform Construction Code. Cite the article or section (for example "NEC 210.8(A)") when you state a code requirement. Say when the township or inspector (the AHJ) may have local amendments, and when you are not sure of an exact section, say so instead of guessing.
- Kohler generators: sizing, models, fuel, transfer switches, placement and clearances, maintenance. For a sizing, use the size_generator tool, which is the same math as the Generators -> Sizing page, and point them to that page.
- This company: look records up with the tools before you answer anything about a client, a job, a quote, a service or a price. Never guess a name, number, status or price; if a tool finds nothing, say so. Mention jobs as "Job #<number>" so they link.
- Safety first: never coach live work that should be de-energized. When something is dangerous, a judgment call for the owner, a warranty or a pricing decision, or beyond what you can confirm, tell them it is worth calling Caleb.
- Keep answers short: a direct answer first, then the few details that matter. Plain text, short paragraphs and simple "- " lists. No markdown headings, tables or bold."""
CALEB_HISTORY = 16
CALEB_TOOL_ROUNDS = 6
CALEB_LIMIT = 8
CALEB_EXAMPLES = (
    "What does Premium mean on our quotes?",
    "What size wire and breaker for a 48 A EV charger?",
    "Which Kohler generator for a 2,600 sq ft home with a 4-ton A/C on natural gas?",
    "Where are we on the Smith job?",
    "Where does GFCI protection have to go in a kitchen remodel?",
)


def caleb_agent():
    return AiAgent.query.filter(db.func.lower(AiAgent.name) == "caleb").first()


def caleb_company_facts(user):
    """What Caleb knows about this company without looking anything up."""
    lines = [
        "HOW QUOTES WORK HERE",
        "A quote offers the client options in tiers, lowest to highest: " + ", ".join(QUOTE_TIERS) + ". A three-option quote uses "
        + ", ".join(THREE_OPTION_TIERS) + ". Each higher tier adds scope or uses a better product than the one below. Caden (the quoting AI) "
        "builds the options from the Services library and the Pricing Sheet; a person confirms the quote and presents it.",
        "Words on a quote: \"Premium installation of ...\" (or \"Premium replacement of ...\") means the upgraded product or higher-quality "
        "version, offered in the higher tiers. \"Turnkey installation of ...\" means a complete new install we supply, wire and finish. "
        "\"Replacement of ...\" swaps old for new, \"Upgrade of ...\" improves what is there, \"Safety inspection of ...\" is checking and testing. "
        "Labor, setup, cleanup, travel and disposal are always included and never listed.",
        "Client-facing names: GFCI = water-protection receptacle/breaker, AFCI = fire-protection breaker, SPD = whole-home surge protector, "
        "tamper-resistant = child-safe receptacle, interlock = generator safety switch, load center = electrical panel.",
        "",
        "JOBS",
        "Statuses: " + "; ".join(label for _, label in JOB_STATUSES) + ".",
        "The Jobs page groups them: In the works (before a quote), Quoted (a confirmed quote waiting on the client), Active (approved, "
        "scheduled or in progress) and Completed (completed or declined). Many clients and jobs are pulled from Jobber.",
        "",
        "WHERE THINGS ARE IN THE SOFTWARE",
        "Quoting -> Generate Quote (Caden builds tiered options), Pricing Sheet (material prices, Cooper prices), Labor Calculator (Mike, labor "
        "times), Services. Clients & Jobs -> Clients, Jobs, Schedule. Sales -> Onsite Checklist (site visits on jobs in the works), Reviews. "
        "Time Tracking -> Time Clock, Timesheet. Bookkeeping -> Tasking (Brendon), Training (Darwin). Generators -> Sizing. Settings is admins only.",
        "",
        "KOHLER AIR-COOLED MODELS ON THE SIZING PAGE (kW on LP / natural gas)",
    ]
    lines += ["- %s: %g / %g kW (%s)" % (model, lp, ng, description) for model, lp, ng, description in gen.KOHLER_MODELS]
    lines.append("Above the largest of these: load management or a Kohler liquid-cooled unit (the 24RCLA and up), sized with Kohler or the distributor.")
    if user.can("view_pricing"):
        lines += ["", "Company labor rate: $%.2f per hour." % get_labor_rate()]
    return "\n".join(lines)


def caleb_tools(user):
    """The look-ups this person's role allows (OpenAI function tools)."""
    def tool(name, description, properties, required=None):
        return {"type": "function", "name": name, "description": description, "strict": True,
                "parameters": {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}}
    text = {"type": "string"}
    number = {"type": "integer"}
    tools = [tool("size_generator", "Size a Kohler home standby generator (the Generators -> Sizing math). Give only what you know; "
                  "leave other numbers null for the page's defaults.",
                  {name: {"type": ["number", "null"], "description": label} for name, (label, *_rest) in gen.FIELDS.items()}
                  | {name: {"type": ["string", "null"], "enum": [value for value, _ in options] + [None]} for name, options in gen.CHOICES.items()})]
    if user.can("view_clients"):
        tools.append(tool("find_clients", "Search clients by name, company, phone, email, address or tag.", {"query": text}))
        tools.append(tool("client_details", "One client's contact details, notes and jobs.", {"client_id": number}))
    if user.can("view_jobs"):
        tools.append(tool("find_jobs", "Search jobs by job number, title, description, client name or address. group narrows to a Jobs tab.",
                          {"query": text, "group": {"type": "string", "enum": ["any", "in_works", "quoted", "active", "completed"]}}))
        tools.append(tool("job_details", "One job: status, client, address, notes, dates, its quotes and their options, site visits and generator sizings.",
                          {"job_id": number}))
    if user.can("view_services"):
        tools.append(tool("find_services", "Search the Services library (what we sell, its parts and labor hours).", {"query": text}))
    if user.can("view_pricing"):
        tools.append(tool("find_price_items", "Search the Pricing Sheet (materials: price, unit, labor hours, Cooper part number).", {"query": text}))
    return tools


def _caleb_words(query):
    return [word for word in re.split(r"\s+", str(query or "").strip()) if word][:6]


def _caleb_client_row(client):
    return {"client_id": client.id, "name": client.full_name, "phone": client.phone, "email": client.email,
            "address": client.primary_address, "tags": client.tag_list, "jobs": len(client.jobs)}


def _caleb_job_row(job):
    return {"job_id": job.id, "job_number": job.job_number, "title": job.title, "client": job.client.full_name if job.client else "",
            "status": job.status_label, "group": job.bucket, "address": job.job_address,
            "updated": job.updated_at.strftime("%Y-%m-%d") if job.updated_at else ""}


def caleb_run_tool(name, args, user):
    """Run one look-up for this person. Read only; a look-up their role does not allow answers with a refusal."""
    allowed = {tool["name"] for tool in caleb_tools(user)}
    if name not in allowed:
        return {"error": "This person's role cannot see that, so do not answer it from guesswork."}
    args = args if isinstance(args, dict) else {}
    if name == "size_generator":
        values = {key: value for key, value in args.items() if value is not None}
        values["calculate"] = "1"
        result = gen.size(values)
        return {"needs_kw": result["required_kw"], "running_kw": result["running_kw"], "starting_kw": result["starting_kw"],
                "starting_set_by": result["sets_start"], "recommended": result["model"],
                "with_load_management": result["with_load_management"], "transfer_switch_amps": result["transfer_switch_amps"],
                "notes": result["notes"], "lines": [{"load": row[0], "running_kw": round(row[1], 2), "starting_kw": round(row[2], 2)} for row in result["rows"]],
                "page": url_for("generator_sizing")}
    if name == "find_clients":
        query = Client.query
        for word in _caleb_words(args.get("query")):
            like = f"%{word}%"
            query = query.filter(db.or_(Client.first_name.ilike(like), Client.last_name.ilike(like), Client.company_name.ilike(like),
                                        Client.phone.ilike(like), Client.email.ilike(like), Client.address_line1.ilike(like),
                                        Client.city.ilike(like), Client.tags.ilike(like)))
        rows = query.order_by(Client.last_name, Client.first_name).limit(CALEB_LIMIT).all()
        return {"clients": [_caleb_client_row(client) for client in rows]} if rows else {"clients": [], "note": "No client matches."}
    if name == "client_details":
        client = db.session.get(Client, int(args.get("client_id") or 0))
        if client is None:
            return {"error": "No such client."}
        row = _caleb_client_row(client)
        row.update({"other_contacts": client.other_contacts, "notes": client.notes[:2000], "lead_source": client.lead_source,
                    "other_addresses": [prop.text for prop in client.properties],
                    "job_list": [_caleb_job_row(job) for job in client.jobs[:20]] if user.can("view_jobs") else "This role cannot see jobs."})
        return row
    if name == "find_jobs":
        query = Job.query.join(Client, Job.client_id == Client.id)
        for word in _caleb_words(args.get("query")):
            like = f"%{word.lstrip('#')}%"
            conditions = [Job.title.ilike(like), Job.description.ilike(like), Client.first_name.ilike(like), Client.last_name.ilike(like),
                          Client.company_name.ilike(like), Job.address_line1.ilike(like), Job.city.ilike(like)]
            if word.lstrip("#").isdigit():
                conditions.append(Job.job_number == int(word.lstrip("#")))
            query = query.filter(db.or_(*conditions))
        jobs = query.order_by(Job.updated_at.desc()).limit(200).all()
        group = args.get("group") or "any"
        if group != "any":
            jobs = [job for job in jobs if job.bucket == group]
        jobs = jobs[:CALEB_LIMIT]
        return {"jobs": [_caleb_job_row(job) for job in jobs]} if jobs else {"jobs": [], "note": "No job matches."}
    if name == "job_details":
        job = db.session.get(Job, int(args.get("job_id") or 0))
        if job is None:
            return {"error": "No such job."}
        row = _caleb_job_row(job)
        prices = user.can("view_quoting")
        row.update({
            "description": job.description[:3000], "internal_notes": job.internal_notes[:2000],
            "assigned_to": job.assigned_user.username if job.assigned_user else "",
            "site_review": job.review_at.strftime("%Y-%m-%d %H:%M") if job.review_at else "",
            "quote_presentation": job.quote_presentation_at.strftime("%Y-%m-%d %H:%M") if job.quote_presentation_at else "",
            "work_start": job.work_start_date.isoformat() if job.work_start_date else "", "duration_days": job.duration_days,
            "client_phone": job.client.phone if job.client and user.can("view_clients") else "",
            "jobber": {"quote_number": job.jobber_quote_number, "quote_status": job.jobber_quote_status,
                       "quote_total": job.jobber_quote_total if prices else None} if job.jobber_id or job.jobber_quote_id else None,
            "quotes": [{"status": quote.status, "revision": quote.revision, "title": quote.title,
                        "created": quote.created_at.strftime("%Y-%m-%d"),
                        "options": [{"tier": option.tier, "title": option.title, "description": option.description, "bullets": option.bullets,
                                     "total": round(option.total, 2) if prices else "hidden for this role",
                                     "chosen": bool(job.material_order and job.material_order.quote_option_id == option.id)}
                                    for option in quote.options]} for quote in job.quotes[:4]],
            "site_visits": [{"date": visit.updated_at.strftime("%Y-%m-%d"), "observations": visit.observations[:1500],
                             "client_priorities": visit.client_priorities[:800]} for visit in job.inspections[:3]],
            "generator_sizings": [sizing.summary for sizing in job.generator_sizings[:3]],
            "page": url_for("job_detail", job_id=job.id),
        })
        return row
    if name == "find_services":
        query = Service.query
        for word in _caleb_words(args.get("query")):
            like = f"%{word}%"
            query = query.filter(db.or_(Service.name.ilike(like), Service.category.ilike(like), Service.description.ilike(like)))
        prices = user.can("view_pricing")
        return {"services": [{"name": service.name, "category": service.category, "description": service.description[:600],
                              "example_quantity": service.example_quantity, "labor_hours": service.estimated_labor_hours,
                              "parts": [item.name for item in service.items][:15],
                              "example_total": round(service.example_total, 2) if prices else "hidden for this role"}
                             for service in query.order_by(Service.name).limit(CALEB_LIMIT).all()]}
    if name == "find_price_items":
        query = PriceItem.query.filter_by(active=True)
        for word in _caleb_words(args.get("query")):
            like = f"%{word}%"
            query = query.filter(db.or_(PriceItem.name.ilike(like), PriceItem.category.ilike(like), PriceItem.cooper_part_number.ilike(like)))
        return {"items": [{"name": item.name, "category": item.category, "unit": item.unit, "price": item.price,
                           "labor_hours_per_unit": item.labor_hours_per_unit, "cooper_part_number": item.cooper_part_number,
                           "unconfirmed": item.category == UNCONFIRMED}
                          for item in query.order_by(PriceItem.name).limit(CALEB_LIMIT).all()]}
    return {"error": "Unknown look-up."}


def _caleb_item(item):
    dump = getattr(item, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else item


def _caleb_describe(name, args):
    if name == "size_generator":
        return "Sized a generator"
    detail = args.get("query") or args.get("job_id") or args.get("client_id") or ""
    return {"find_clients": "Searched clients", "client_details": "Read a client", "find_jobs": "Searched jobs", "job_details": "Read a job",
            "find_services": "Searched services", "find_price_items": "Searched the Pricing Sheet"}.get(name, name) + (f": {detail}" if detail else "")


def ask_caleb(user, history):
    """Caleb's answer to the last message in history, and what he looked at."""
    if OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("The OpenAI API is not configured.")
    agent = caleb_agent()
    instructions = f"""{build_agent_context(agent)}

The person asking is {user.username} (role: {user.role.name if user.role else 'none'}). Today is {datetime.utcnow():%B %d, %Y}.
Look-ups you have are limited to what this person's role may see; if they ask for something you cannot look up, say their role cannot see it.

{caleb_company_facts(user)}"""
    tools = caleb_tools(user) + [{"type": "web_search"}]
    client = OpenAI()
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    conversation = list(history)
    looked = []
    for _ in range(CALEB_TOOL_ROUNDS):
        try:
            response = client.responses.create(model=model, instructions=instructions, input=conversation, tools=tools)
        except Exception as error:  # noqa: BLE001
            if not any(tool.get("type") == "web_search" for tool in tools) or "web_search" not in str(error):
                raise
            tools = [tool for tool in tools if tool.get("type") != "web_search"]       # this account or model has no web search
            response = client.responses.create(model=model, instructions=instructions, input=conversation, tools=tools)
        calls = [item for item in (getattr(response, "output", None) or []) if getattr(item, "type", "") == "function_call"]
        if any(getattr(item, "type", "") == "web_search_call" for item in (getattr(response, "output", None) or [])):
            looked.append("Searched the web")
        if not calls:
            return (response.output_text or "").strip(), looked
        conversation += [_caleb_item(item) for item in response.output]
        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
            except ValueError:
                args = {}
            try:
                result = caleb_run_tool(call.name, args, user)
            except Exception:  # noqa: BLE001
                app.logger.exception("Caleb look-up %s failed", call.name)
                result = {"error": "That look-up failed."}
            looked.append(_caleb_describe(call.name, args))
            conversation.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, default=str)[:30000]})
    response = client.responses.create(model=model, instructions=instructions, tool_choice="none", tools=tools,
                                       input=conversation + [{"role": "user", "content": "Answer now with what you found."}])
    return (response.output_text or "").strip(), looked


def caleb_links(text):
    """Escape an answer and link every "Job #N" the reader may open."""
    html = str(escape(text or ""))
    if not (current_user.is_authenticated and current_user.can("view_jobs")):
        return Markup(html)
    numbers = {int(n) for n in re.findall(r"Job #(\d+)", html)}
    jobs = {job.job_number: job.id for job in Job.query.filter(Job.job_number.in_(numbers)).all()} if numbers else {}
    return Markup(re.sub(r"Job #(\d+)", lambda m: f'<a href="{url_for("job_detail", job_id=jobs[int(m.group(1))])}">{m.group(0)}</a>'
                         if int(m.group(1)) in jobs else m.group(0), html))


app.jinja_env.filters["caleb_links"] = caleb_links


@app.route("/caleb")
@login_required
def caleb_page():
    messages = CalebMessage.query.filter_by(user_id=current_user.id, cleared=False).order_by(CalebMessage.created_at, CalebMessage.id).all()[-60:]
    agent = caleb_agent()
    ready = bool(OpenAI is not None and os.environ.get("OPENAI_API_KEY") and agent and agent.active)
    return render_template("caleb.html", messages=messages, ready=ready, agent=agent, examples=CALEB_EXAMPLES,
                           sees=[label for permission, label in (("view_clients", "clients"), ("view_jobs", "jobs and quotes"),
                                                                 ("view_services", "services"), ("view_pricing", "prices"))
                                 if current_user.can(permission)])


@app.route("/caleb/ask", methods=["POST"])
@login_required
def caleb_ask():
    check_csrf()
    question = request.form.get("question", "").strip()[:4000]
    if not question:
        return redirect(url_for("caleb_page"))
    db.session.add(CalebMessage(user_id=current_user.id, role="user", content=question)); db.session.commit()
    agent = caleb_agent()
    looked = []
    if agent is None or not agent.active or OpenAI is None or not os.environ.get("OPENAI_API_KEY"):
        answer = "I'm not connected right now (the AI connection or my settings are off), so call Caleb for this one. Your question was saved."
    else:
        rows = CalebMessage.query.filter_by(user_id=current_user.id, cleared=False).order_by(CalebMessage.created_at.desc(), CalebMessage.id.desc()).limit(CALEB_HISTORY).all()[::-1]
        try:
            answer, looked = ask_caleb(current_user, [{"role": row.role, "content": row.content} for row in rows])
            answer = answer or "I couldn't put an answer together. Try asking another way, or call Caleb."
        except Exception as error:  # noqa: BLE001
            app.logger.exception("Ask Caleb failed")
            answer = f"I couldn't answer that just now. {describe_ai_error(error)}"
    db.session.add(CalebMessage(user_id=current_user.id, role="assistant", content=answer, looked_up="\n".join(looked)))
    db.session.commit()
    return redirect(url_for("caleb_page") + "#latest")


@app.route("/caleb/new", methods=["POST"])
@login_required
def caleb_new():
    check_csrf()
    CalebMessage.query.filter_by(user_id=current_user.id, cleared=False).update({"cleared": True}, synchronize_session=False)
    db.session.commit()
    return redirect(url_for("caleb_page"))


# ---------------- Releases (Settings -> Releases) ----------------
# Builds come straight from GitHub onto the live site; there is no test site.
# The newest build on the branch goes live by itself (GitHub's webhook, or a look
# every few minutes while someone is using the site). Any older build can be put
# back by hand: a rollback, which pauses auto-deploy until it is switched back on.
# The machinery lives in releases.py; this block owns the order things happen in.

import releases as rel

RELEASE_DIR = os.path.join(basedir, "instance", "releases")
RELEASE_BUILDS = os.path.join(RELEASE_DIR, "builds")
RELEASE_BACKUPS = os.path.join(basedir, "instance", "backups")
RELEASE_INFO = os.path.join(basedir, "instance", "release.json")
RELEASE_LOOK_EVERY = timedelta(minutes=5)
RELEASE_SOURCES = {"auto": "Auto-deploy", "manual": "Deployed by hand", "rollback": "Rollback", "snapshot": "Restore"}
_release_lock = rel.Lock(os.path.join(RELEASE_DIR, "deploy.lock"))
_release_memory = {"looked": None, "builds": None, "builds_at": None}


def release_setting(key, default=""):
    row = db.session.get(CompanySetting, "releases_" + key)
    return row.value if row and row.value is not None else default


def put_release_setting(key, value, commit=True):
    row = db.session.get(CompanySetting, "releases_" + key) or CompanySetting(key="releases_" + key)
    row.value = str(value)
    db.session.add(row)
    if commit:
        db.session.commit()


def release_secret(key):
    stored = release_setting(key)
    if not stored:
        return ""
    try:
        return supplier_cipher().decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def put_release_secret(key, value, commit=True):
    put_release_setting(key, supplier_cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else "", commit)


def release_conn():
    return release_secret("token"), release_setting("repo"), release_setting("branch", "main") or "main"


def release_connected():
    token, repo, _branch = release_conn()
    return bool(token and repo)


def release_live():
    return Release.query.filter_by(status="live").order_by(Release.finished_at.desc()).first()


def release_running():
    """The deploy in progress, if any. One left 'running' for over 30 minutes died with its worker."""
    running = Release.query.filter_by(status="running").order_by(Release.started_at.desc()).first()
    if running and datetime.utcnow() - running.started_at > timedelta(minutes=30):
        running.status, running.finished_at = "failed", datetime.utcnow()
        running.message = (running.message + "\n" if running.message else "") + "Stopped without finishing (the web worker restarted)."
        db.session.commit()
        return None
    return running


def release_auto_on():
    return release_setting("auto", "1") == "1"


def release_info():
    try:
        with open(RELEASE_INFO, encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _asset_version():
    info = release_info()
    if info.get("sha"):
        return info["sha"][:7]
    try:
        return str(int(os.path.getmtime(os.path.join(basedir, "static", "css", "style.css"))))
    except OSError:
        return "1"


ASSET_VERSION = _asset_version()     # appended to CSS/JS links so a deploy never shows stale styling
app.jinja_env.globals["asset_version"] = ASSET_VERSION


def _release_log(release, line):
    release.message = (release.message + "\n" if release.message else "") + line
    db.session.commit()


def start_release(sha, source, by_label, zip_path="", meta=None):
    """Record the deploy and run it in the background. Returns (release or None, sentence)."""
    running = release_running()
    if running:
        return None, "A deploy is already running (%s, started %s UTC). Wait for it to finish." % (
            running.title or running.short, running.started_at.strftime("%-I:%M %p"))
    if _release_lock.busy():
        _release_lock.clear()            # no deploy is marked running, so this lock was left by a stopped worker
    meta = meta or {}
    release = Release(sha=sha or "", source=source, by_label=by_label[:120], status="running", zip_path=zip_path,
                      title=(meta.get("title") or "")[:250], notes=meta.get("notes") or "", author=(meta.get("author") or "")[:120],
                      committed_at=meta.get("date"), started_at=datetime.utcnow())
    db.session.add(release)
    db.session.commit()
    worker = threading.Thread(target=_release_worker, args=(release.id,), daemon=True)
    worker.start()
    return release, "Deploying %s…" % (release.title or release.short)


def _release_worker(release_id):
    with app.app_context():
        release = db.session.get(Release, release_id)
        if release is None:
            return
        others = Release.query.filter(Release.status == "running", Release.id != release.id).count()
        if not others:
            _release_lock.clear()        # nothing else is deploying: any lock here is left over
        if not _release_lock.acquire():
            release.status, release.finished_at = "failed", datetime.utcnow()
            _release_log(release, "Another deploy was already running, so this one did not start.")
            return
        staged = ""
        try:
            staged = _release_deploy(release)
        except (rel.ReleaseError, OSError, ValueError, zipfile.BadZipFile) as exc:
            db.session.rollback()
            release = db.session.get(Release, release_id)
            release.status, release.finished_at = "failed", datetime.utcnow()
            _release_log(release, "Not deployed: %s" % exc)
        except Exception as exc:  # noqa: BLE001 — a deploy never takes the web worker down
            db.session.rollback()
            release = db.session.get(Release, release_id)
            release.status, release.finished_at = "failed", datetime.utcnow()
            _release_log(release, "Not deployed: unexpected %s: %s" % (exc.__class__.__name__, exc))
        finally:
            if staged:
                shutil.rmtree(staged, ignore_errors=True)
            _release_lock.release()
        # Reload last, with the lock already let go: on PythonAnywhere the reload restarts this very worker,
        # so nothing after it can be relied on to run.
        release = db.session.get(Release, release_id)
        if release is not None and release.status == "live":
            _release_log(release, "Reloading the web app…")
            ok, said = rel.reload_web_app(release_setting("wsgi_path"))
            _release_log(release, said)


def _release_deploy(release):
    """Download, check, back up, copy, record, reload. Nothing live changes until the build has passed its checks."""
    token, repo, _branch = release_conn()
    if release.zip_path and os.path.isfile(release.zip_path):
        zip_path = release.zip_path
    else:
        if not release.sha:
            raise rel.ReleaseError("That snapshot's zip is no longer on the server.")
        zip_path = rel.download(token, repo, release.sha, RELEASE_BUILDS)
        release.zip_path = zip_path
    _release_log(release, "Build downloaded." if release.sha else "Snapshot found.")
    staged = rel.stage(zip_path, RELEASE_DIR)
    release.version = rel.read_version(staged)
    _release_log(release, "Build checked: every Python file compiles.")

    # The very first deploy from GitHub keeps the code that was uploaded by hand, so it can be put back.
    if not Release.query.filter(Release.status.in_(("live", "replaced"))).first():
        snapshot = rel.snapshot_code(basedir, os.path.join(RELEASE_BUILDS, "before-github.zip"))
        db.session.add(Release(source="snapshot", status="replaced", title="Code that was live before GitHub releases",
                               by_label="Saved automatically", zip_path=snapshot, started_at=datetime.utcnow(),
                               finished_at=datetime.utcnow(), message="Saved before the first deploy from GitHub."))
        _release_log(release, "Saved the code that was live before GitHub releases (it can be restored below).")

    release.db_backup = rel.backup_database(os.path.join(basedir, "instance", "app.db"), RELEASE_BACKUPS,
                                            "before-" + (release.version or release.short))
    if release.db_backup:
        _release_log(release, "Database backed up: instance/backups/%s" % os.path.basename(release.db_backup))

    if rel.file_hash(os.path.join(staged, "requirements.txt")) != rel.file_hash(os.path.join(basedir, "requirements.txt")):
        _release_log(release, "requirements.txt changed: installing packages…")
        ok, output = rel.pip_install(staged)
        _release_log(release, ("Packages installed." if ok else "Package install failed:\n" + output))
        if not ok:
            raise rel.ReleaseError("the packages in requirements.txt could not be installed; the live site was not changed.")

    try:
        previous = json.loads(release_setting("manifest", "[]") or "[]")
    except ValueError:
        previous = []
    changed, removed, manifest = rel.sync(staged, basedir, previous)
    release.files_changed, release.files_removed = changed, removed
    put_release_setting("manifest", json.dumps(manifest), commit=False)
    for other in Release.query.filter(Release.status == "live", Release.id != release.id).all():
        other.status = "replaced"
    release.status, release.finished_at = "live", datetime.utcnow()
    with open(RELEASE_INFO, "w", encoding="utf-8") as handle:
        json.dump({"sha": release.sha, "version": release.version, "title": release.title, "release_id": release.id,
                   "deployed_at": release.finished_at.isoformat(timespec="seconds")}, handle, indent=1)
    _release_log(release, "%d file%s updated, %d removed." % (changed, "" if changed == 1 else "s", removed))
    return staged


def release_check(by_label="GitHub", force=False):
    """Look at the branch; put its newest build live when auto-deploy is on. Returns a sentence. Never raises."""
    token, repo, branch = release_conn()
    if not (token and repo):
        return "GitHub is not connected yet."
    put_release_setting("looked_at", datetime.utcnow().isoformat(timespec="seconds"))
    if release_setting("hook_id") and release_setting("hook_events") != "2":
        try:
            rel.upgrade_hook(token, repo, release_setting("hook_id"))
            put_release_setting("hook_events", "2")
        except rel.ReleaseError:
            pass                                 # the token cannot edit webhooks: the regular check still works
    try:
        newest = rel.head(token, repo, branch)
    except rel.ReleaseError as exc:
        put_release_setting("error", str(exc))
        return str(exc)
    put_release_setting("error", "")
    _release_memory["builds_at"] = None
    live = release_live()
    if live and live.sha == newest["sha"]:
        return "The live site already runs the newest build (%s)." % newest["short"]
    if not release_auto_on() and not force:
        return "A newer build is on GitHub (%s). Auto-deploy is paused, so it is waiting for you." % newest["short"]
    if not force and Release.query.filter_by(sha=newest["sha"], status="failed", source="auto").first():
        return "The newest build (%s) already failed to deploy; it will not be retried by itself." % newest["short"]
    try:
        ok, why = rel.passed(token, repo, newest["sha"], branch)
    except rel.ReleaseError as exc:
        put_release_setting("error", str(exc))
        return str(exc)
    if not ok:
        put_release_setting("waiting", "%s: %s" % (newest["short"], why))
        return "The newest build (%s) is waiting: %s. Only builds whose tests passed go live by themselves." % (newest["short"], why)
    put_release_setting("waiting", "")
    release, said = start_release(newest["sha"], "auto", by_label, meta=newest)
    return said


def _release_check_in_background(by_label):
    def run():
        with app.app_context():
            try:
                release_check(by_label)
            except Exception:  # noqa: BLE001
                db.session.rollback()
    threading.Thread(target=run, daemon=True).start()


@app.before_request
def release_poll():
    """While anyone is using the site, look at GitHub every few minutes (the webhook is the quick way)."""
    if not current_user.is_authenticated or request.endpoint in (None, "static", "releases_status"):
        return
    now = datetime.utcnow()
    if _release_memory["looked"] and now - _release_memory["looked"] < RELEASE_LOOK_EVERY:
        return
    _release_memory["looked"] = now
    if not release_connected() or not release_auto_on():
        return
    try:
        last = datetime.fromisoformat(release_setting("looked_at")) if release_setting("looked_at") else None
    except ValueError:
        last = None
    if last and now - last < RELEASE_LOOK_EVERY:
        return
    put_release_setting("looked_at", now.isoformat(timespec="seconds"))
    _release_check_in_background("Auto-deploy (regular check)")


def release_builds(force=False):
    """The branch's builds, newest first, remembered for a minute. (builds, error)."""
    token, repo, branch = release_conn()
    if not (token and repo):
        return [], ""
    now = datetime.utcnow()
    if not force and _release_memory["builds_at"] and now - _release_memory["builds_at"] < timedelta(seconds=60):
        return _release_memory["builds"], ""
    try:
        builds = rel.list_builds(token, repo, branch)
    except rel.ReleaseError as exc:
        return [], str(exc)
    try:
        badges = rel.test_badges(token, repo, branch)
    except rel.ReleaseError:
        badges = {}
    for build in builds:
        build["tests"] = badges.get(build["sha"], "")
    _release_memory["builds"], _release_memory["builds_at"] = builds, now
    return builds, ""


@app.route("/settings/releases")
@login_required
def releases_page():
    wsgi = release_setting("wsgi_path")
    if not wsgi or not os.path.isfile(wsgi):
        found = rel.find_wsgi(request.host)
        if found != wsgi:
            put_release_setting("wsgi_path", found)
            wsgi = found
    running = release_running()
    live = release_live()
    builds, builds_error = release_builds(force=request.args.get("refresh") == "1")
    history = Release.query.order_by(Release.started_at.desc()).limit(40).all()
    by_sha = {}
    for row in sorted(history, key=lambda r: r.started_at):
        if row.sha:
            by_sha[row.sha] = row
    live_index = next((i for i, b in enumerate(builds) if live and b["sha"] == live.sha), None)
    for index, build in enumerate(builds):
        last = by_sha.get(build["sha"])
        build["last_status"] = last.status if last else ""
        build["last_message"] = last.message.splitlines()[-1] if last and last.message else ""
        if live and build["sha"] == live.sha:
            build["place"] = "live"
        elif live_index is None:
            build["place"] = "newest" if index == 0 else "older"
        else:
            build["place"] = "newer" if index < live_index else "older"
    token, repo, branch = release_conn()
    return render_template("releases.html", live=live, running=running, builds=builds, builds_error=builds_error,
                           history=history, sources=RELEASE_SOURCES, connected=bool(token and repo), repo=repo, branch=branch,
                           token_hint=("ends in " + token[-4:]) if len(token) >= 8 else ("saved" if token else ""),
                           auto_on=release_auto_on(), paused_note=release_setting("paused_note"),
                           hook_on=bool(release_setting("hook_id")), hook_url=url_for("releases_github_hook", _external=True, _scheme="https"),
                           looked_at=release_setting("looked_at"), check_error=release_setting("error"), wsgi_path=wsgi,
                           waiting=release_setting("waiting"),
                           newer_waiting=bool(builds and live and builds[0]["sha"] != live.sha), info=release_info())


@app.route("/settings/releases/status")
@login_required
def releases_status():
    running = release_running()
    live = release_live()
    latest = Release.query.order_by(Release.started_at.desc()).first()
    return jsonify({"running": bool(running), "live": live.short if live else "",
                    "log": (running or latest).message.splitlines()[-8:] if (running or latest) else [],
                    "latest_status": latest.status if latest else ""})


@app.route("/settings/releases/connect", methods=["POST"])
@login_required
def releases_connect():
    check_csrf()
    repo = rel.clean_repo(request.form.get("repo", ""))
    branch = (request.form.get("branch", "") or "main").strip() or "main"
    token = "".join(request.form.get("token", "").split())
    if not repo:
        flash("The repository is owner/name, like 19rhouseknecht/Powerhouse.", "error")
        return redirect(url_for("releases_page"))
    old_token, old_repo, _old_branch = release_conn()
    token = token or old_token
    if not token:
        flash("Paste a GitHub token to connect.", "error")
        return redirect(url_for("releases_page"))
    try:
        found = rel.gh("GET", "/repos/{repo}", token, repo)
    except rel.ReleaseError as exc:
        flash(str(exc), "error")
        return redirect(url_for("releases_page"))
    if old_repo and old_repo != repo and release_setting("hook_id"):
        try:
            rel.delete_hook(old_token, old_repo, release_setting("hook_id"))
        except rel.ReleaseError:
            pass
        put_release_setting("hook_id", "", commit=False)
    put_release_secret("token", token, commit=False)
    put_release_setting("repo", found.get("full_name") or repo, commit=False)
    put_release_setting("branch", branch, commit=False)
    secret = release_secret("hook_secret") or secrets.token_hex(24)
    put_release_secret("hook_secret", secret, commit=False)
    db.session.commit()
    said = "Connected to %s (%s)." % (found.get("full_name") or repo, branch)
    try:
        hook_id = rel.create_hook(token, repo, url_for("releases_github_hook", _external=True, _scheme="https"), secret,
                                  release_setting("hook_id"))
        put_release_setting("hook_id", hook_id)
        put_release_setting("hook_events", "2")
        said += " GitHub will tell this site the moment a new build lands."
    except rel.ReleaseError as exc:
        put_release_setting("hook_id", "")
        said += " No instant webhook (%s) — the site will still look every few minutes." % str(exc).rstrip(".")
    _release_memory["builds_at"] = None
    flash(said, "success")
    return redirect(url_for("releases_page"))


@app.route("/settings/releases/disconnect", methods=["POST"])
@login_required
def releases_disconnect():
    check_csrf()
    token, repo, _branch = release_conn()
    if release_setting("hook_id"):
        try:
            rel.delete_hook(token, repo, release_setting("hook_id"))
        except rel.ReleaseError:
            pass
    for key in ("token", "hook_secret", "hook_id", "repo"):
        put_release_setting(key, "", commit=False)
    db.session.commit()
    _release_memory["builds_at"] = None
    flash("GitHub disconnected. The live site keeps running the build it has.", "success")
    return redirect(url_for("releases_page"))


@app.route("/settings/releases/check", methods=["POST"])
@login_required
def releases_check():
    check_csrf()
    flash(release_check("Checked by %s" % current_user.username), "success")
    return redirect(url_for("releases_page"))


@app.route("/settings/releases/deploy", methods=["POST"])
@login_required
def releases_deploy():
    check_csrf()
    sha = request.form.get("sha", "").strip()
    if not (len(sha) == 40 and all(ch in "0123456789abcdef" for ch in sha)):
        abort(400)
    token, repo, _branch = release_conn()
    try:
        meta = rel.commit(token, repo, sha)
    except rel.ReleaseError as exc:
        flash(str(exc), "error")
        return redirect(url_for("releases_page"))
    builds, _error = release_builds()
    is_newest = bool(builds) and builds[0]["sha"] == sha
    source = "manual" if is_newest else "rollback"
    release, said = start_release(sha, source, current_user.username, meta=meta)
    if release and source == "rollback":
        put_release_setting("auto", "0", commit=False)
        put_release_setting("paused_note", "Paused because %s rolled back to %s on %s." % (
            current_user.username, meta["short"], datetime.utcnow().strftime("%b %d, %Y %H:%M UTC")))
    elif release and is_newest:
        put_release_setting("paused_note", "")
    flash(said, "success" if release else "error")
    return redirect(url_for("releases_page"))


@app.route("/settings/releases/<int:release_id>/restore", methods=["POST"])
@login_required
def releases_restore(release_id):
    check_csrf()
    snapshot = db.get_or_404(Release, release_id)
    if snapshot.source != "snapshot" or not os.path.isfile(snapshot.zip_path):
        flash("That snapshot can no longer be restored.", "error")
        return redirect(url_for("releases_page"))
    release, said = start_release("", "snapshot", current_user.username, zip_path=snapshot.zip_path,
                                  meta={"title": snapshot.title})
    if release:
        put_release_setting("auto", "0", commit=False)
        put_release_setting("paused_note", "Paused because %s restored the pre-GitHub code." % current_user.username)
    flash(said, "success" if release else "error")
    return redirect(url_for("releases_page"))


@app.route("/settings/releases/auto", methods=["POST"])
@login_required
def releases_auto():
    check_csrf()
    turn_on = request.form.get("on") == "1"
    put_release_setting("auto", "1" if turn_on else "0", commit=False)
    put_release_setting("paused_note", "" if turn_on else "Paused by %s." % current_user.username)
    if turn_on:
        flash("Auto-deploy is on. " + release_check("Auto-deploy (switched on by %s)" % current_user.username), "success")
    else:
        flash("Auto-deploy is paused. New builds will wait here until you deploy them.", "success")
    return redirect(url_for("releases_page"))


@app.route("/releases/github-hook", methods=["POST"])
def releases_github_hook():
    """GitHub's ping when the branch changes. Only a correctly signed push to the branch does anything."""
    body = request.get_data(cache=False) or b""
    if not rel.signature_ok(release_secret("hook_secret"), body, request.headers.get("X-Hub-Signature-256", "")):
        return jsonify({"ok": False, "said": "bad signature"}), 403
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return jsonify({"ok": True, "said": "pong"})
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        return jsonify({"ok": False, "said": "not json"}), 400
    _token, _repo, branch = release_conn()
    if event == "workflow_run":          # the tests finished: a build that was waiting for them may go now
        run = payload.get("workflow_run") or {}
        if payload.get("action") != "completed" or run.get("name") != rel.CHECK_NAME or run.get("head_branch") != branch:
            return jsonify({"ok": True, "said": "not the release branch's tests"})
    elif event != "push" or payload.get("ref") != "refs/heads/" + branch:
        return jsonify({"ok": True, "said": "not the release branch"})
    _release_check_in_background("Auto-deploy (GitHub webhook)")
    return jsonify({"ok": True, "said": "checking"}), 202


# Create tables on import — this runs whether the app is started with
# `python app.py` locally or loaded by the WSGI server on PythonAnywhere,
# which never executes the __main__ block below.
with app.app_context():
    def add_column_if_missing(table_name, column_name, sql_definition):
        columns = {row[1] for row in db.session.execute(text(f"PRAGMA table_info({table_name})"))}
        if columns and column_name not in columns:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_definition}"))

    existing_service_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(service)"))}
    if existing_service_columns and "estimated_labor_hours" not in existing_service_columns:
        db.session.execute(text("ALTER TABLE service ADD COLUMN estimated_labor_hours FLOAT NOT NULL DEFAULT 0"))
        db.session.commit()
    existing_training_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(training)"))}
    if existing_training_columns and "description" not in existing_training_columns:
        db.session.execute(text("ALTER TABLE training ADD COLUMN description TEXT NOT NULL DEFAULT ''"))
        db.session.execute(text("UPDATE training SET description = notes WHERE description = '' AND notes != ''"))
        db.session.execute(text("UPDATE training SET notes = '' WHERE description != ''"))
        db.session.commit()
    for table, column, sql in [
        ("client", "updated_at", "DATETIME"), ("client", "company_name", "VARCHAR(180) NOT NULL DEFAULT ''"),
        ("client", "other_contacts", "TEXT NOT NULL DEFAULT ''"), ("client", "tags", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ("client", "lead_source", "VARCHAR(120) NOT NULL DEFAULT ''"), ("client", "jobber_id", "VARCHAR(80) NOT NULL DEFAULT ''"),
        ("client", "jobber_url", "VARCHAR(255) NOT NULL DEFAULT ''"), ("client", "jobber_raw", "TEXT NOT NULL DEFAULT ''"),
        ("client", "jobber_synced_at", "DATETIME"),
        ("job", "source", "VARCHAR(20) NOT NULL DEFAULT ''"), ("job", "jobber_id", "VARCHAR(80) NOT NULL DEFAULT ''"),
        ("job", "jobber_number", "VARCHAR(20) NOT NULL DEFAULT ''"), ("job", "jobber_url", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ("job", "jobber_quote_id", "VARCHAR(80) NOT NULL DEFAULT ''"), ("job", "jobber_quote_number", "VARCHAR(20) NOT NULL DEFAULT ''"),
        ("job", "jobber_quote_status", "VARCHAR(40) NOT NULL DEFAULT ''"), ("job", "jobber_quote_total", "FLOAT"),
        ("job", "jobber_request_id", "VARCHAR(80) NOT NULL DEFAULT ''"), ("job", "jobber_raw", "TEXT NOT NULL DEFAULT ''"),
        ("job", "jobber_synced_at", "DATETIME"),
        ("quote", "pricing_needed_json", "TEXT NOT NULL DEFAULT ''"),
        ("price_item", "image_path", "VARCHAR(255) NOT NULL DEFAULT ''"), ("price_item", "image_source", "VARCHAR(20) NOT NULL DEFAULT ''"),
        ("price_sync_run", "item_ids_json", "TEXT NOT NULL DEFAULT ''"),
        ("price_sync_run", "mode", "VARCHAR(20) NOT NULL DEFAULT ''"),
        ("price_sync_run", "limit_items", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        add_column_if_missing(table, column, sql)
    add_column_if_missing("user", "role_id", "INTEGER")
    add_column_if_missing("user", "hourly_rate", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("job_visit", "hourly_rate", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("job", "job_number", "INTEGER")
    add_column_if_missing("job", "contract_amount", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("job", "material_cost", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("job", "drive_minutes", "FLOAT")
    add_column_if_missing("job", "drive_distance_miles", "FLOAT")
    add_column_if_missing("job", "drive_checked_at", "DATETIME")
    add_column_if_missing("job", "drive_error", "VARCHAR(255) NOT NULL DEFAULT ''")
    add_column_if_missing("job", "drive_minutes_override", "FLOAT")
    add_column_if_missing("role", "view_sales", "BOOLEAN NOT NULL DEFAULT 1")
    add_column_if_missing("quote_option_service", "price_item_id", "INTEGER")
    add_column_if_missing("quote_option_service", "material_unit_price", "FLOAT")
    add_column_if_missing("quote_option_service", "labor_hours_per_unit", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("quote_option_service", "labor_rate_snapshot", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("quote_option_service", "manual_priced", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing("quote_option_service", "manually_edited", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing("quote_option", "manually_edited", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing("quote_option", "manual_total_override", "FLOAT")
    add_column_if_missing("price_item", "active", "BOOLEAN NOT NULL DEFAULT 1")
    add_column_if_missing("price_item", "labor_hours_per_unit", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("ai_agent", "prompt", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("onsite_inspection", "home_age", "TEXT NOT NULL DEFAULT 'unknown'")
    add_column_if_missing("onsite_inspection", "top_sellers_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing("quote", "option_count", "INTEGER NOT NULL DEFAULT 6")
    add_column_if_missing("quote", "ai_snapshot_json", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing("quote", "travel_minutes_snapshot", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("quote", "travel_cost_snapshot", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("quote", "travel_origin_snapshot", "VARCHAR(255) NOT NULL DEFAULT ''")
    add_column_if_missing("quote", "travel_destination_snapshot", "VARCHAR(255) NOT NULL DEFAULT ''")
    add_column_if_missing("quote", "travel_method_snapshot", "VARCHAR(40) NOT NULL DEFAULT 'estimated'")
    add_column_if_missing("quick_quote", "option_count", "INTEGER NOT NULL DEFAULT 6")
    add_column_if_missing("service_tool", "tool_id", "INTEGER")
    add_column_if_missing("service_tool", "tool_type", "VARCHAR(80) NOT NULL DEFAULT 'Other'")
    add_column_if_missing("service_tool", "rental_cost", "FLOAT NOT NULL DEFAULT 0")
    add_column_if_missing("price_sync_run", "only_zero_prices", "BOOLEAN NOT NULL DEFAULT 0")
    db.session.commit()
    db.create_all()
    # Keep existing scopes and prices in their original positions while moving
    # every quote to the current Economy-through-Platinum tier standard.
    for existing_quote in Quote.query.all():
        expected_tiers = quote_tiers(existing_quote.option_count)
        for position, option in enumerate(sorted(existing_quote.options, key=lambda row: row.position)):
            if position < len(expected_tiers):
                option.tier = expected_tiers[position]
    for service_tool in ServiceTool.query.filter_by(tool_id=None).order_by(ServiceTool.id).all():
        lookup_key = normalize_item_name(service_tool.name)
        catalog_tool = ToolCatalog.query.filter_by(lookup_key=lookup_key).first() if lookup_key else None
        if catalog_tool is None and lookup_key:
            catalog_tool = ToolCatalog(name=service_tool.name, lookup_key=lookup_key, tool_type=service_tool.tool_type or "Other", rental_cost=max(service_tool.rental_cost or 0, 0))
            db.session.add(catalog_tool); db.session.flush()
        if catalog_tool:
            service_tool.tool_id = catalog_tool.id
            service_tool.name = catalog_tool.name
            service_tool.tool_type = catalog_tool.tool_type
            service_tool.rental_cost = catalog_tool.rental_cost
    role_defaults = {
        "Admin": dict(is_admin=True, view_pricing=True, view_all_schedules=True, view_all_timesheets=True, view_finance=True, manage_users=True, manage_agents=True),
        "Office": dict(view_quoting=True, view_pricing=True, view_services=True, view_clients=True, view_jobs=True, view_schedule=True, view_all_schedules=True, view_time=True, view_all_timesheets=True, view_tasks=True, view_training=True, view_finance=True, view_expenses=True, view_sales=True),
        "Foreman": dict(view_quoting=True, view_pricing=False, view_services=True, view_clients=True, view_jobs=True, view_schedule=True, view_all_schedules=True, view_time=True, view_all_timesheets=False, view_tasks=True, view_training=True, view_finance=False, view_expenses=True, view_sales=True),
        "Installer": dict(view_quoting=False, view_pricing=False, view_services=True, view_clients=False, view_jobs=True, view_schedule=True, view_all_schedules=False, view_time=True, view_all_timesheets=False, view_tasks=True, view_training=True, view_finance=False, view_expenses=True, view_sales=True),
    }
    for role_name, defaults in role_defaults.items():
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name, **defaults); db.session.add(role)
    db.session.flush()
    admin_role = Role.query.filter_by(name="Admin").first()
    for user in User.query.filter_by(role_id=None).all(): user.role = admin_role
    next_number = 700
    for job in Job.query.order_by(Job.created_at, Job.id).all():
        if job.job_number is None:
            while Job.query.filter_by(job_number=next_number).first(): next_number += 1
            job.job_number = next_number; next_number += 1
    if CompanySetting.query.filter_by(key="labor_rate").first() is None:
        db.session.add(CompanySetting(key="labor_rate", value="361.90"))
    if CompanySetting.query.filter_by(key="dispatch_address").first() is None:
        db.session.add(CompanySetting(key="dispatch_address", value=DEFAULT_DISPATCH_ADDRESS))
    if CompanySetting.query.filter_by(key="travel_road_factor").first() is None:
        db.session.add(CompanySetting(key="travel_road_factor", value="1.25"))
    if CompanySetting.query.filter_by(key="travel_average_speed").first() is None:
        db.session.add(CompanySetting(key="travel_average_speed", value="45"))
    estimator_version = CompanySetting.query.filter_by(key="travel_estimator_version").first()
    if estimator_version is None or estimator_version.value != "census-free-v1":
        Job.query.filter(Job.drive_minutes_override.is_(None)).update({"drive_minutes":None, "drive_distance_miles":None, "drive_checked_at":None, "drive_error":""}, synchronize_session=False)
        Quote.query.filter_by(status="draft").update({"travel_minutes_snapshot":0, "travel_cost_snapshot":0, "travel_origin_snapshot":"", "travel_destination_snapshot":"", "travel_method_snapshot":"estimated"}, synchronize_session=False)
        if estimator_version is None:
            estimator_version = CompanySetting(key="travel_estimator_version")
            db.session.add(estimator_version)
        estimator_version.value = "census-free-v1"
    if AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden").first() is None:
        db.session.add(AiAgent(name="Caden", role="Powerhouse Quoting AI Agent", identity="You are a thoughtful Powerhouse Electrical Solutions quoting agent. Value service, safety, honesty, and long-term customer relationships over maximizing the price of a job. Use clear homeowner-friendly language and never pressure a client toward the most expensive option.", prompt=DEFAULT_CADEN_PROMPT))
    if AiAgent.query.filter(db.func.lower(AiAgent.name) == "darwin").first() is None:
        db.session.add(AiAgent(name="Darwin", role="Powerhouse Training AI Agent", identity="You organize training knowledge into clear, accurate, practical lessons. Summarize what was actually documented, preserve important safety details, and answer questions from the training record without inventing facts."))
    if AiAgent.query.filter(db.func.lower(AiAgent.name) == "brendon").first() is None:
        db.session.add(AiAgent(name="Brendon", role="Powerhouse Tasking AI Agent", identity="You turn quick conversations into organized, accurately assigned work. Match existing tasks carefully, ask when a person or task is ambiguous, and report exactly what you created, updated, or completed."))
    if AiAgent.query.filter(db.func.lower(AiAgent.name) == "caleb").first() is None:
        db.session.add(AiAgent(name="Caleb", role="Powerhouse Help AI Agent (Ask Caleb)", identity=CALEB_IDENTITY, prompt=CALEB_PROMPT))
    if AiAgent.query.filter(db.func.lower(AiAgent.name) == "mike").first() is None:
        db.session.add(AiAgent(name="Mike", role="Powerhouse Labor Estimating AI Agent", identity="You are Power House Electrical Solutions' labor-time specialist. Ground estimates in uploaded labor references, existing Pricing Sheet labor, and Service labor. State assumptions clearly, distinguish evidence from inference, and never change central labor values without human preview and approval.", prompt="Use uploaded labor manuals as the primary estimating standard. Match recommendations to exact Pricing Sheet items and their stored unit. Show current versus proposed hours, cite a supporting excerpt, and allow a human to approve every update. For questions, explain access, distance, wall or raceway conditions, crew size, setup, and uncertainty. Never invent precision or alter material pricing."))
    caden_agent = AiAgent.query.filter(db.func.lower(AiAgent.name) == "caden").first()
    if caden_agent and not caden_agent.prompt.strip():
        caden_agent.prompt = DEFAULT_CADEN_PROMPT
    elif caden_agent and "PRICING REPAIR AND LABOR MEMORY:" not in caden_agent.prompt:
        caden_agent.prompt = caden_agent.prompt.rstrip() + "\n\n" + CADEN_LABOR_PROMPT_ADDENDUM
    if caden_agent and "MANUAL QUOTE EDITS:" not in caden_agent.prompt:
        caden_agent.prompt = caden_agent.prompt.rstrip() + "\n\n" + CADEN_MANUAL_EDIT_ADDENDUM
    if caden_agent and "OPTION STRATEGY, INCLUDED LABOR, AND SALES OPPORTUNITIES:" not in caden_agent.prompt:
        caden_agent.prompt = caden_agent.prompt.rstrip() + "\n\n" + CADEN_OPTION_STRATEGY_ADDENDUM
    if caden_agent and "DIRECT USER-SUPPLIED PRICING:" not in caden_agent.prompt:
        caden_agent.prompt = caden_agent.prompt.rstrip() + "\n\n" + CADEN_DIRECT_PRICING_ADDENDUM
    if caden_agent and "CLIENT WORDING (plain language):" not in caden_agent.prompt:
        if CADEN_PRESENTATION_WORDING_V1 in caden_agent.prompt:      # the old block, untouched: replace it
            caden_agent.prompt = caden_agent.prompt.replace(CADEN_PRESENTATION_WORDING_V1, CADEN_CLIENT_WORDING_ADDENDUM)
        else:                                                         # edited by hand, or never added: add the new one
            caden_agent.prompt = caden_agent.prompt.rstrip() + "\n\n" + CADEN_CLIENT_WORDING_ADDENDUM
    if caden_agent:
        caden_agent.prompt = caden_agent.prompt.replace(
            "Always create exactly six tiers in this order: Basic, Bronze, Silver, Gold, Diamond, Platinum.",
            "Create exactly the selected three or six tiers in the order supplied by the application."
        ).replace(
            "A six-option quote uses Basic, Bronze, Silver, Gold, Diamond, Platinum. A three-option quote uses Basic, Gold, Platinum.",
            "A six-option quote uses Economy, Basic, Bronze, Silver, Gold, Platinum. A three-option quote uses Economy, Gold, Platinum."
        ).replace("still build all six options", "still build every selected option")
    # Preserve every legacy Cooper number while exposing the new multi-supplier model.
    for item in PriceItem.query.filter(PriceItem.cooper_part_number != "").all():
        legacy_number = (item.cooper_part_number or "").strip()
        if legacy_number and not any("cooper" in canonical_item_name(row.supplier) and canonical_part_number(row.part_number) == canonical_part_number(legacy_number) for row in item.supplier_parts):
            item.supplier_parts.append(SupplierPartNumber(supplier="Cooper Electric", part_number=legacy_number))
    # Drafts created before labor-aware quote lines existed are safe to refresh.
    # Confirmed quote snapshots remain untouched.
    draft_rate = get_labor_rate()
    for draft in Quote.query.filter_by(status="draft").all():
        for option in draft.options:
            for line in option.lines:
                if line.material_unit_price is not None:
                    continue
                if line.service:
                    line.material_unit_price = line.service.material_total
                    line.labor_hours_per_unit = line.service.estimated_labor_hours
                    line.labor_rate_snapshot = draft_rate
                    line.unit_price = line.service.example_total
                elif line.price_item:
                    line.material_unit_price = line.price_item.price
                    line.labor_hours_per_unit = line.price_item.labor_hours_per_unit
                    line.labor_rate_snapshot = draft_rate
                    line.unit_price = line.price_item.price + (line.price_item.labor_hours_per_unit * draft_rate)
    db.session.commit()
    # One-time, non-destructive bridge for services created before the pricing
    # sheet existed. Existing line items become centralized catalog items.
    changed = False
    for line in ServiceItem.query.all():
        if line.pricing_link:
            continue
        catalog = PriceItem.query.filter_by(lookup_key=normalize_item_name(line.name), active=True).first()
        if catalog is None:
            catalog = PriceItem(name=line.name, lookup_key=normalize_item_name(line.name), unit=line.unit or "ea", price=line.unit_price or 0)
            db.session.add(catalog)
            db.session.flush()
        line.pricing_link = ServiceItemPricingLink(price_item=catalog)
        changed = True
    if changed:
        db.session.commit()

if __name__ == "__main__":
    app.run(debug=True)
