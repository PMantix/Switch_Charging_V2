// Fleet Dashboard — WebSocket client + table row renderer
"use strict";

const SEQUENCES = [
    "1: all-off",
    "2: [0,1,2,3]",
    "3: [0,1,3,2]",
    "4: [0,2,1,3]",
    "5: [0,2,3,1]",
    "6: [0,3,1,2]",
    "7: [0,3,2,1]",
    "8: all-on",
];

const MODES = ["charge", "discharge", "pulse_charge", "idle"];
// Auto-follow can only target a charging mode (server rejects others).
const AF_TARGETS = ["charge", "pulse_charge"];

let fleetState = {};
let ws = null;

// --- WebSocket ---

function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById("ws-status").textContent = "connected";
        document.getElementById("ws-status").style.color = "var(--green)";
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "fleet") {
            updateFleet(msg.pis);
        }
    };

    ws.onclose = () => {
        document.getElementById("ws-status").textContent = "disconnected";
        document.getElementById("ws-status").style.color = "var(--red)";
        setTimeout(connectWS, 3000);
    };

    ws.onerror = () => ws.close();
}

// --- Fleet rendering ---

function updateFleet(pis) {
    const tbody = document.getElementById("fleet-body");
    const emptyMsg = document.getElementById("empty-msg");

    if (pis.length === 0) {
        emptyMsg.style.display = "";
        return;
    }
    emptyMsg.style.display = "none";
    document.getElementById("pi-count").textContent = pis.length;

    for (const pi of pis) {
        fleetState[pi.pi_num] = pi;
        let row = document.getElementById(`row-${pi.pi_num}`);
        if (!row) {
            row = createRow(pi);
            // Insert sorted by pi_num
            const rows = tbody.querySelectorAll("tr");
            let inserted = false;
            for (const existing of rows) {
                const existingNum = parseInt(existing.dataset.pinum);
                if (pi.pi_num < existingNum) {
                    tbody.insertBefore(row, existing);
                    inserted = true;
                    break;
                }
            }
            if (!inserted) tbody.appendChild(row);
        }
        updateRow(row, pi);
    }
}

function createRow(pi) {
    const n = pi.pi_num;
    const row = document.createElement("tr");
    row.id = `row-${n}`;
    row.dataset.pinum = n;
    row.innerHTML = `
        <td class="pi-name">SW${n}</td>
        <td data-f="status"></td>
        <td data-f="mode" class="editable" onclick="openEdit(${n},'mode')"></td>
        <td data-f="freq" class="editable" onclick="openEdit(${n},'frequency')"></td>
        <td data-f="seq" class="editable" onclick="openEdit(${n},'sequence')"></td>
        <td data-f="af-en" class="editable" onclick="openEdit(${n},'af-enabled')"></td>
        <td data-f="af-tgt" class="editable" onclick="openEdit(${n},'af-target')"></td>
        <td data-f="af-cc" class="editable" onclick="openEdit(${n},'af-cc')"></td>
        <td data-f="af-ie" class="editable" onclick="openEdit(${n},'af-ienter')"></td>
        <td data-f="af-ix" class="editable" onclick="openEdit(${n},'af-iexit')"></td>
        <td data-f="af-act"></td>
        <td data-f="avg"></td>
        <td data-f="age"></td>
    `;
    return row;
}

function updateRow(row, pi) {
    row.className = "";
    if (pi.status === "offline") row.classList.add("offline");
    if (pi.age_s !== null && pi.age_s > 60) row.classList.add("stale");

    // Status
    const statusTd = row.querySelector('[data-f="status"]');
    statusTd.innerHTML =
        `<span class="status-badge ${pi.status}">${pi.status}</span>` +
        ` <button class="btn-refresh" title="Re-poll SW${pi.pi_num}"` +
        ` onclick="refreshPi(${pi.pi_num})">&#x21bb;</button>`;

    // Main fields
    setCell(row, "mode", pi.mode || "--");
    setCell(row, "freq", pi.frequency != null ? pi.frequency : "--");
    setCell(row, "seq", pi.sequence != null ? seqLabel(pi.sequence) : "--");

    // Auto-follow
    const af = pi.auto_follow;
    if (af) {
        setCell(row, "af-en", af.enabled ? "ON" : "OFF");
        setCell(row, "af-tgt", af.target_mode || "--");
        setCell(row, "af-cc", af.cc_setpoint_a != null ? af.cc_setpoint_a : "--");
        setCell(row, "af-ie", af.i_enter_a != null ? af.i_enter_a : "--");
        setCell(row, "af-ix", af.i_exit_a != null ? af.i_exit_a : "--");

        const actTd = row.querySelector('[data-f="af-act"]');
        if (af.active) {
            actTd.innerHTML = `<span class="af-active-badge active">ACTIVE</span>`;
        } else if (af.enabled) {
            actTd.innerHTML = `<span class="af-active-badge armed">ARMED</span>`;
        } else {
            actTd.innerHTML = `<span class="af-active-badge off">OFF</span>`;
        }

        const avgI = af.avg_current_a != null ? af.avg_current_a.toFixed(4) : "?";
        const avgV = af.avg_voltage_v != null ? af.avg_voltage_v.toFixed(2) : "?";
        setCell(row, "avg", `${avgI} / ${avgV}`);
    } else {
        setCell(row, "af-en", "--");
        setCell(row, "af-tgt", "--");
        setCell(row, "af-cc", "--");
        setCell(row, "af-ie", "--");
        setCell(row, "af-ix", "--");
        setCell(row, "af-act", "--");
        setCell(row, "avg", "--");
    }

    // Grey out (and disable editing of) the auto-follow setting cells when
    // auto-follow is off or its state is unknown. The AF enable toggle itself
    // (af-en) stays active so it can be turned back on.
    const afEnabled = !!(af && af.enabled);
    for (const name of ["af-tgt", "af-cc", "af-ie", "af-ix"]) {
        const el = row.querySelector(`[data-f="${name}"]`);
        if (el) el.classList.toggle("cell-disabled", !afEnabled);
    }

    // Frequency is meaningless on the static sequences (all-off at index 0,
    // all-on at the last index) — there is no switching cycle to clock.
    const staticSeq = pi.sequence === 0 || pi.sequence === SEQUENCES.length - 1;
    const freqEl = row.querySelector('[data-f="freq"]');
    if (freqEl) freqEl.classList.toggle("cell-disabled", staticSeq);

    setCell(row, "age", pi.age_s != null ? formatAge(pi.age_s) : "never");
}

function setCell(row, name, value) {
    const el = row.querySelector(`[data-f="${name}"]`);
    if (el) el.textContent = value;
}

function seqLabel(seq) {
    if (Array.isArray(seq)) return JSON.stringify(seq);
    // The Pi reports a 0-based sequence index (0-7), matching the TUI and
    // server convention; the SEQUENCES labels already carry the 1-based
    // human number ("8: all-on"), so index directly with the raw value.
    if (typeof seq === "number" && seq >= 0 && seq < SEQUENCES.length)
        return SEQUENCES[seq];
    return String(seq);
}

function formatAge(seconds) {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
}

// --- Edit modal ---

function openEdit(piNum, field) {
    const pi = fleetState[piNum];
    if (!pi || pi.status === "offline") {
        showToast("Pi is offline", "error");
        return;
    }

    const modal = document.getElementById("edit-modal");
    const title = document.getElementById("edit-title");
    const body = document.getElementById("edit-body");
    const submitBtn = document.getElementById("edit-submit");

    title.textContent = `Pi SW${piNum}`;
    body.innerHTML = "";

    let buildCmd;

    switch (field) {
        case "mode":
            body.innerHTML = buildSelect("Mode", "mode", MODES, pi.mode);
            buildCmd = () => ({ cmd: "set_mode", mode: val("mode") });
            break;
        case "frequency":
            body.innerHTML = buildInput("Frequency (Hz)", "freq", pi.frequency || 10, "number", "0.1", "2000");
            buildCmd = () => ({ cmd: "set_frequency", frequency: parseFloat(val("freq")) });
            break;
        case "sequence": {
            const cur = pi.sequence ?? 0;
            const seqOpts = SEQUENCES.map((label, i) =>
                `<option value="${i}" ${i === cur ? "selected" : ""}>${label}</option>`
            ).join("");
            body.innerHTML = `<div class="modal-field">
                <label>Sequence</label>
                <select id="edit-seq">${seqOpts}</select>
            </div>`;
            // value is the 0-based index — send it straight through.
            buildCmd = () => ({ cmd: "set_sequence", sequence: parseInt(val("seq")) });
            break;
        }
        case "af-enabled":
            body.innerHTML = buildSelect("Auto-Follow", "afen", ["true", "false"],
                pi.auto_follow?.enabled ? "true" : "false");
            buildCmd = () => ({ cmd: "auto_follow_set_enabled", enabled: val("afen") === "true" });
            break;
        case "af-target":
            body.innerHTML = buildSelect("Target Mode", "aftgt", AF_TARGETS,
                pi.auto_follow?.target_mode || "charge");
            buildCmd = () => ({ cmd: "auto_follow_set_target", target_mode: val("aftgt") });
            break;
        case "af-cc":
            body.innerHTML = buildInput("CC Setpoint (A)", "afcc",
                pi.auto_follow?.cc_setpoint_a || 0, "number", "0", "10");
            buildCmd = () => ({ cmd: "auto_follow_set_cc_setpoint", cc_setpoint_a: parseFloat(val("afcc")) });
            break;
        case "af-ienter":
        case "af-iexit":
            body.innerHTML =
                buildInput("I Enter (A)", "ie", pi.auto_follow?.i_enter_a || 0.05, "number", "0", "10") +
                buildInput("I Exit (A)", "ix", pi.auto_follow?.i_exit_a || 0.02, "number", "0", "10");
            buildCmd = () => ({
                cmd: "auto_follow_set_thresholds",
                i_enter_a: parseFloat(val("ie")),
                i_exit_a: parseFloat(val("ix")),
            });
            break;
        default:
            return;
    }

    submitBtn.onclick = async () => {
        const cmd = buildCmd();
        closeModal();
        await sendCommand(piNum, cmd);
    };

    modal.classList.add("visible");
}

function closeModal() {
    document.getElementById("edit-modal").classList.remove("visible");
}

function buildSelect(label, id, options, current) {
    const opts = options.map(o => {
        const sel = String(o) === String(current) ? "selected" : "";
        return `<option value="${o}" ${sel}>${o}</option>`;
    }).join("");
    return `<div class="modal-field">
        <label>${label}</label>
        <select id="edit-${id}">${opts}</select>
    </div>`;
}

function buildInput(label, id, current, type, min, max) {
    return `<div class="modal-field">
        <label>${label}</label>
        <input id="edit-${id}" type="${type}" value="${current}"
               ${min ? `min="${min}"` : ""} ${max ? `max="${max}"` : ""} step="any">
    </div>`;
}

function val(id) {
    return document.getElementById(`edit-${id}`).value;
}

// --- Batch operations ---

function batchSend() {
    const field = document.getElementById("batch-field").value;
    const value = document.getElementById("batch-value").value;
    if (!value) { showToast("Enter a value", "error"); return; }

    let cmd;
    switch (field) {
        case "mode":
            cmd = { cmd: "set_mode", mode: value };
            break;
        case "frequency":
            cmd = { cmd: "set_frequency", frequency: parseFloat(value) };
            break;
        case "sequence":
            cmd = { cmd: "set_sequence", sequence: parseInt(value) };
            break;
        case "af-enabled":
            cmd = { cmd: "auto_follow_set_enabled", enabled: value === "true" };
            break;
        case "af-target":
            cmd = { cmd: "auto_follow_set_target", target_mode: value };
            break;
        case "af-cc":
            cmd = { cmd: "auto_follow_set_cc_setpoint", cc_setpoint_a: parseFloat(value) };
            break;
        default:
            showToast("Unknown field", "error");
            return;
    }

    const onlinePis = Object.values(fleetState).filter(p => p.status !== "offline");
    if (onlinePis.length === 0) {
        showToast("No Pis online", "error");
        return;
    }

    for (const pi of onlinePis) {
        sendCommand(pi.pi_num, cmd);
    }
    showToast(`Queued for ${onlinePis.length} Pi(s)`, "info");
}

function updateBatchValueInput() {
    const field = document.getElementById("batch-field").value;
    const container = document.getElementById("batch-value-container");
    switch (field) {
        case "mode":
            container.innerHTML = `<select id="batch-value">
                ${MODES.map(m => `<option value="${m}">${m}</option>`).join("")}
            </select>`;
            break;
        case "frequency":
            container.innerHTML = `<input id="batch-value" type="number" value="10" min="0.1" max="2000" step="any" placeholder="Hz">`;
            break;
        case "sequence":
            container.innerHTML = `<select id="batch-value">
                ${SEQUENCES.map((s, i) => `<option value="${i}">${s}</option>`).join("")}
            </select>`;
            break;
        case "af-enabled":
            container.innerHTML = `<select id="batch-value">
                <option value="true">ON</option>
                <option value="false">OFF</option>
            </select>`;
            break;
        case "af-target":
            container.innerHTML = `<select id="batch-value">
                ${AF_TARGETS.map(m => `<option value="${m}">${m}</option>`).join("")}
            </select>`;
            break;
        case "af-cc":
            container.innerHTML = `<input id="batch-value" type="number" value="0" min="0" max="10" step="any" placeholder="Amps">`;
            break;
    }
}

// --- Refresh (force re-poll) ---

async function refreshAll() {
    try {
        const resp = await fetch("/api/refresh", { method: "POST" });
        const data = await resp.json();
        if (data.ok) showToast("Refreshing all Pis…", "info");
        else showToast(data.error || "refresh failed", "error");
    } catch (e) {
        showToast("network error", "error");
    }
}

async function refreshPi(piNum) {
    try {
        const resp = await fetch(`/api/pi/${piNum}/refresh`, { method: "POST" });
        const data = await resp.json();
        if (data.ok) showToast(`SW${piNum}: refreshing…`, "info");
        else showToast(`SW${piNum}: ${data.error || "refresh failed"}`, "error");
    } catch (e) {
        showToast(`SW${piNum}: network error`, "error");
    }
}

// --- API ---

async function sendCommand(piNum, cmd) {
    try {
        const resp = await fetch(`/api/pi/${piNum}/command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(cmd),
        });
        const data = await resp.json();
        if (data.ok) {
            showToast(`SW${piNum}: ${cmd.cmd} queued`, "success");
        } else {
            showToast(`SW${piNum}: ${data.error}`, "error");
        }
    } catch (e) {
        showToast(`SW${piNum}: network error`, "error");
    }
}

// --- Toast ---

function showToast(message, type = "info") {
    const container = document.getElementById("toasts");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// --- Init ---

document.addEventListener("DOMContentLoaded", () => {
    connectWS();
    updateBatchValueInput();
    document.getElementById("batch-field").addEventListener("change", updateBatchValueInput);

    document.getElementById("edit-modal").addEventListener("click", (e) => {
        if (e.target.classList.contains("modal-backdrop")) closeModal();
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeModal();
    });
});
