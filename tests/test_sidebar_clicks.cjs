/**
 * Browser-free click-path tests. A JS syntax check alone did not detect the
 * original event-handler typo that broke every PalPulse sidebar button.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const registrations = new Map();
const effects = {
  navigate: [],
  alerts: [],
  confirms: [],
  service: [],
  webSocket: [],
  downloads: [],
  blobs: [],
};

class FakeHTMLElement {
  attachShadow() {
    const root = {
      innerHTML: "",
      listeners: new Map(),
      addEventListener(name, handler) {
        this.listeners.set(name, handler);
      },
      querySelectorAll(selector) { return selector === "[data-setting]" ? (this.fields || []) : []; },
    };
    this.shadowRoot = root;
    return root;
  }
}
class FakeBlob {
  constructor(parts, options) {
    this.parts = parts;
    this.options = options;
    effects.blobs.push(this);
  }
}
const sandbox = {
  HTMLElement: FakeHTMLElement,
  Blob: FakeBlob,
  URL: {
    createObjectURL() { return "blob:palpulse-test"; },
    revokeObjectURL() {},
  },
  customElements: {
    get(name) { return registrations.get(name); },
    define(name, ctor) { registrations.set(name, ctor); },
  },
  window: {
    location: { assign(url) { effects.navigate.push(url); } },
    confirm(message) {
      effects.confirms.push(message);
      return true;
    },
    alert(message) { effects.alerts.push(message); },
  },
  document: {
    createElement(tag) {
      assert.equal(tag, "a");
      const a = {
        href: "", download: "",
        click() { effects.downloads.push({ href: a.href, name: a.download }); },
      };
      return a;
    },
  },
  setInterval() { return 1; },
  clearInterval() {},
  setTimeout() { return 1; },
  Date,
  Intl,
  console,
};

const frontendPath = path.resolve(
  __dirname, "../custom_components/palpulse/frontend/palpulse-panel.js"
);
vm.runInNewContext(fs.readFileSync(frontendPath, "utf8"), sandbox, {
  filename: frontendPath,
});
const Panel = registrations.get("palpulse-control-centre");
assert.ok(Panel, "sidebar must register its web component");

const energy = "palpulse:powerpal_d8a5d6d91964_energy";
const cost = "palpulse:powerpal_d8a5d6d91964_cost";
const state = {
  meters: [{
    entry_id: "meter-one",
    title: "Test Powerpal",
    meter: "d8a5d6d91964",
    energy: { status: "Active", last_completed_utc_hour: 1794420000 },
    cost: { status: "Active", last_costed_hour: 1794420000 },
    pricing: { pricing_mode: "custom_time_of_use" },
    settings_revision: "version-one",
    tariff_versions: [{ effective_from: "2026-09-15" }],
    settings: { pricing_mode: "custom_time_of_use", peak_cents_per_kwh: 35, offpeak_cents_per_kwh: 20, daily_supply_cents: 118, daily_free_cap_kwh: 15, free_excess_cents_per_kwh: 25 },
    migration: { stage: "completed" },
    cost_migration: { stage: "completed" },
    statistics: { energy, cost },
    recent: {
      [energy]: [{ start: 1794412800, change: 0.8, sum: 100 }],
      [cost]: [{ start: 1794412800, change: 0.18, sum: 5 }],
    },
  }],
};

const panel = new Panel();
panel._data = state;
panel._meter = "meter-one";
const fakeHass = {
  states: {},
  services: { backup: { create_automatic: {} } },
  async callService(domain, service, data) {
    effects.service.push({ domain, service, data });
  },
  async callWS(message) {
    effects.webSocket.push(message);
    if (message.type === "palpulse/panel_info") {
      return state;
    }
    if (message.type === "palpulse/link_verified_cost") {
      return { linked: true, message: "PalPulse cost statistic linked" };
    }
    if (message.type === "palpulse/save_settings") {
      return { saved: true, message: "Settings saved", settings_revision: "version-two" };
    }
    if (message.type === "palpulse/cost_reconcile_preview") {
      return { ok: true, plan: {
        digest: "c".repeat(64), verified_hours: 4, retained_hours: 0,
        baseline_verified: true, repair_safe: true, cost_counter_conflicts: [],
        blocking_conflicts: [], end_utc: 1794489600,
        missing_energy_hours: 0, last_sum: 12.34,
        free_uncertain_dates: [], issues: [],
        day_totals: [{ day: "2026-11-09", recorded: 2.65,
                       recomputed: 10.34, verified_hours: 24,
                       retained_hours: 0, missing_hours: 0,
                       recorded_complete: true }],
      }};
    }
    if (message.type === "palpulse/cost_reconcile_resume") {
      return { started: true, message: "Previous verified repair resumed" };
    }
    if (message.type === "palpulse/cost_reconcile_apply") {
      return { applied: true, changed_hours: 4,
               message: "Cost rows verified and checkpoint updated." };
    }
    if (message.type === "palpulse/replay_checkpoint") {
      return { accepted: true, message: "Replay request received" };
    }
    throw Error("Unexpected websocket command");
  },
};

function action(fields) {
  return {
    dataset: fields,
    hasAttribute(name) {
      return Object.hasOwn(fields, name.substring("data-".length));
    },
    closest() { return this; },
  };
}

async function click(fields) {
  const listener = panel.shadowRoot.listeners.get("click");
  assert.equal(typeof listener, "function", "delegated click listener must exist");
  return listener({ target: action(fields) });
}

(async () => {
  panel.connectedCallback();
  // Assign after mounting so the initial lifecycle has no background refresh
  // racing the explicit button-path assertions.
  panel._hass = fakeHass;

  // All seven tabs share the same delegated click handler.
  const sections = [
    ["overview", "Recent energy"],
    ["electricity", "Electricity tariff settings"],
    ["history", "Recorded hourly history"],
    ["reconcile", "Historical cost reconciliation"],
    ["checkpoint", "ESP32 checkpoint"],
    ["settings", "Connections and dashboard"],
    ["migration", "Electricity usage"],
    ["backups", "Home Assistant backup"],
  ];
  for (const [tab, heading] of sections) {
    await click({ tab });
    assert.equal(panel._tab, tab, tab + " click did not navigate");
    assert.ok(panel.shadowRoot.innerHTML.includes(heading), tab + " did not render");
  }

  // Inline editing must submit to the guarded WebSocket endpoint,
  // rather than navigating away or silently altering source state.
  await click({ tab: "electricity" });
  assert.ok(panel.shadowRoot.innerHTML.includes('data-setting="free_excess_cents_per_kwh"'));
  panel.shadowRoot.fields = [{
    dataset: { setting: "free_excess_cents_per_kwh" },
    type: "number", value: "19.75",
  }];
  // The 60-second automatic refresh must not discard an unsaved tariff edit.
  const inputListener = panel.shadowRoot.listeners.get("input");
  assert.equal(typeof inputListener, "function");
  inputListener({ target: panel.shadowRoot.fields[0] });
  panel.shadowRoot.fields = [];
  await panel.refresh();
  assert.ok(panel.shadowRoot.innerHTML.includes('value="19.75"'),
            "automatic refresh discarded a draft electricity rate");
  await click({ "settings-save": "" });
  const save = effects.webSocket.find(x => x.type === "palpulse/save_settings");
  assert.equal(save.entry_id, "meter-one");
  assert.equal(save.expected_revision, "version-one");
  assert.equal(save.changes.free_excess_cents_per_kwh, "19.75");
  assert.ok(effects.navigate.length === 0, "saving must stay inside sidebar");
  panel.shadowRoot.fields = [];

  // Historical reconciliation is explicitly previewed in the sidebar,
  // and Apply sends the precise verified digest with confirmation.
  await click({ tab: "reconcile" });
  await click({ "cost-full": "" });
  assert.equal(panel._costFrom, "2026-09-15", "full-contract button must choose oldest saved tariff");
  await click({ "cost-preview": "" });
  assert.ok(panel.shadowRoot.innerHTML.includes("recorded versus recomputed costs"));
  assert.ok(panel.shadowRoot.innerHTML.includes("2026-11-09"));
  assert.ok(panel.shadowRoot.innerHTML.includes("10.34"));
  await click({ "cost-apply": "" });
  const previewCall = effects.webSocket.find(x => x.type === "palpulse/cost_reconcile_preview");
  const applyCall = effects.webSocket.find(x => x.type === "palpulse/cost_reconcile_apply");
  assert.equal(previewCall.entry_id, "meter-one");
  assert.equal(applyCall.entry_id, "meter-one");
  assert.equal(applyCall.digest, "c".repeat(64));
  assert.equal(applyCall.expected_revision, "version-one");
  assert.equal(applyCall.confirm, true);
  assert.ok(effects.confirms.some(x => x.includes("Recorder-inclusive backup")));

  // A stored interrupted repair must provide a direct resume action, with
  // its original protected dates rather than a confusing date-range error.
  state.meters[0].cost_repair = {
    status: "writing", resume_required: true,
    start_date: "2026-11-05", end_date: "2026-11-10",
    rows_committed: 12, total_rows: 144,
  };
  await panel.refresh();
  assert.ok(panel.shadowRoot.innerHTML.includes("Continue previous repair"));
  await click({ "cost-resume": "" });
  assert.ok(effects.webSocket.some(x => x.type === "palpulse/cost_reconcile_resume" && x.confirm === true));
  state.meters[0].cost_repair = {
    status: "failed", resume_required: true,
    start_date: "2026-11-10", end_date: "2026-11-13",
    rows_committed: 12, total_rows: 66, failed_stage: "verifying_recorder_batches",
    last_error: "ValueError: Live cost checkpoint and Recorder sum disagree",
  };
  await panel.refresh();
  assert.ok(panel.shadowRoot.innerHTML.includes("Cost repair stopped"));
  assert.ok(panel.shadowRoot.innerHTML.includes("12 / 66"));
  assert.ok(panel.shadowRoot.innerHTML.includes("Live cost checkpoint and Recorder sum disagree"));

  await click({ linkcost: "" });
  assert.ok(effects.webSocket.some(x => x.type === "palpulse/link_verified_cost" && x.confirm === true));
  await click({ refresh: "" });
  assert.ok(effects.webSocket.some(x => x.type === "palpulse/panel_info"));

  await click({ route: "/config/integrations/integration/palpulse" });
  assert.deepEqual(effects.navigate, ["/config/integrations/integration/palpulse"]);

  await click({ csv: "" });
  assert.deepEqual(effects.downloads, [
    { href: "blob:palpulse-test", name: "palpulse-hourly-audit.csv" },
  ]);
  assert.ok(effects.blobs[0].parts.join("").includes("0.8,0.18"));

  await click({ backup: "" });
  assert.equal(effects.service[0].domain, "backup");
  assert.equal(effects.service[0].service, "create_automatic");
  assert.ok(effects.alerts.some(x => x.includes("Check Backups")));

  await click({ replay: "" });
  const replay = effects.webSocket.find(x => x.type === "palpulse/replay_checkpoint");
  assert.equal(replay.entry_id, "meter-one");
  assert.equal(replay.confirm, true);
  assert.ok(effects.confirms.length >= 2, "backup and replay need confirmation");
  assert.ok(effects.alerts.some(x => x.includes("Replay request received")));

  // Unrelated clicks should not call any action.
  const before = effects.webSocket.length;
  await panel.shadowRoot.listeners.get("click")({
    target: { closest: () => null },
  });
  assert.equal(effects.webSocket.length, before);

  panel.disconnectedCallback();
  console.log("PalPulse sidebar button paths passed: tabs, settings save, refresh, links, CSV, backup and replay.");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
