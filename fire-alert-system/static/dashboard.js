/**
 * dashboard.js — Real-Time Telemetry & Alert Controller
 * Manages SSE live streaming, Chart.js dual-axis visualization,
 * status cards, calibration tracking, and dynamic log tables.
 */

// ---------------------------------------------------------------------------
// Global State & Configuration
// ---------------------------------------------------------------------------
const MAX_CHART_POINTS = 35;
let telemetryChart = null;
let eventSource = null;
let fallbackPollInterval = null;

// Chart Data Buffers
const chartLabels = [];
const chartTempData = [];
const chartMQ2Data = [];

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initLucide();
  initChart();
  fetchInitialData();
  fetchRecipients();
  initSSE();
  setupEventListeners();
});

function initLucide() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

// ---------------------------------------------------------------------------
// Chart.js Dual-Axis Configuration
// ---------------------------------------------------------------------------
function initChart() {
  const ctx = document.getElementById("telemetryChart").getContext("2d");

  // Create subtle gradient fills
  const tempGradient = ctx.createLinearGradient(0, 0, 0, 300);
  tempGradient.addColorStop(0, "rgba(249, 115, 22, 0.35)");
  tempGradient.addColorStop(1, "rgba(249, 115, 22, 0.0)");

  const mq2Gradient = ctx.createLinearGradient(0, 0, 0, 300);
  mq2Gradient.addColorStop(0, "rgba(168, 85, 247, 0.4)");
  mq2Gradient.addColorStop(1, "rgba(168, 85, 247, 0.0)");

  telemetryChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: chartLabels,
      datasets: [
        {
          label: "MQ-2 Smoke Level",
          data: chartMQ2Data,
          borderColor: "#c084fc",
          backgroundColor: mq2Gradient,
          borderWidth: 2.5,
          tension: 0.3,
          fill: true,
          pointRadius: 3,
          pointHoverRadius: 6,
          pointBackgroundColor: "#c084fc",
          yAxisID: "yMQ2",
        },
        {
          label: "Temperature (°C)",
          data: chartTempData,
          borderColor: "#f97316",
          backgroundColor: tempGradient,
          borderWidth: 2,
          borderDash: [4, 4],
          tension: 0.3,
          fill: false,
          pointRadius: 2,
          pointHoverRadius: 5,
          pointBackgroundColor: "#f97316",
          yAxisID: "yTemp",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: {
            color: "#94a3b8",
            font: { family: "Inter", size: 12 },
            usePointStyle: true,
            boxWidth: 8,
          },
        },
        tooltip: {
          backgroundColor: "rgba(15, 23, 42, 0.9)",
          titleColor: "#f1f5f9",
          bodyColor: "#94a3b8",
          borderColor: "rgba(255, 255, 255, 0.1)",
          borderWidth: 1,
          padding: 10,
          boxPadding: 4,
          usePointStyle: true,
        },
      },
      scales: {
        x: {
          grid: {
            color: "rgba(255, 255, 255, 0.04)",
          },
          ticks: {
            color: "#64748b",
            font: { family: "JetBrains Mono", size: 10 },
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
          },
        },
        yMQ2: {
          type: "linear",
          display: true,
          position: "left",
          grid: {
            color: "rgba(255, 255, 255, 0.05)",
          },
          ticks: {
            color: "#c084fc",
            font: { family: "JetBrains Mono", size: 10 },
          },
          title: {
            display: true,
            text: "MQ-2 Smoke (ADC)",
            color: "#c084fc",
            font: { size: 11, weight: "600" },
          },
        },
        yTemp: {
          type: "linear",
          display: true,
          position: "right",
          grid: {
            drawOnChartArea: false, // only draw grid lines for first y-axis
          },
          ticks: {
            color: "#fb923c",
            font: { family: "JetBrains Mono", size: 10 },
          },
          title: {
            display: true,
            text: "Temp (°C)",
            color: "#fb923c",
            font: { size: 11, weight: "600" },
          },
        },
      },
    },
  });
}

function pushChartPoint(timeStr, temp, mq2) {
  if (!telemetryChart) return;

  chartLabels.push(timeStr);
  chartTempData.push(temp);
  chartMQ2Data.push(mq2);

  if (chartLabels.length > MAX_CHART_POINTS) {
    chartLabels.shift();
    chartTempData.shift();
    chartMQ2Data.shift();
  }

  telemetryChart.update("none"); // High-performance update without animation hitch
}

// ---------------------------------------------------------------------------
// Server-Sent Events (SSE) Live Feed
// ---------------------------------------------------------------------------
function initSSE() {
  if (!!window.EventSource) {
    eventSource = new EventSource("/api/stream");

    eventSource.onopen = () => {
      setConnectionStatus(true, "SSE Connected");
      if (fallbackPollInterval) {
        clearInterval(fallbackPollInterval);
        fallbackPollInterval = null;
      }
    };

    eventSource.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        handleStreamPayload(payload);
      } catch (err) {
        console.error("Error parsing SSE payload:", err);
      }
    };

    eventSource.onerror = () => {
      setConnectionStatus(false, "Reconnecting (Polling active)");
      startFallbackPolling();
    };
  } else {
    // Fallback if EventSource not supported
    startFallbackPolling();
  }
}

function startFallbackPolling() {
  if (!fallbackPollInterval) {
    fallbackPollInterval = setInterval(fetchInitialData, 2000);
  }
}

function setConnectionStatus(connected, text) {
  const badge = document.getElementById("connStatusBadge");
  const label = document.getElementById("connStatusText");
  label.textContent = text;
  if (connected) {
    badge.className = "badge badge-pulse badge-connected";
  } else {
    badge.className = "badge badge-neutral";
  }
}

// ---------------------------------------------------------------------------
// Event Stream Dispatcher
// ---------------------------------------------------------------------------
function handleStreamPayload(data) {
  const type = data.type;

  if (type === "initial_state") {
    if (data.mode) setSystemMode(data.mode);
    if (data.calibration) renderCalibration(data.calibration);
    if (data.phase) renderPhase(data.phase, data.phase_details, data.seconds_remaining);
    if (data.latest_reading) renderReading(data.latest_reading, false);
  } else if (type === "new_reading") {
    renderReading(data.event, true);
  } else if (type === "safety_suppressed_update") {
    markLatestEventSuppressed();
  } else if (type === "phase_update") {
    renderPhase(data.phase, data.details, data.seconds_remaining);
  } else if (type === "calibration_update") {
    renderCalibration(data.calibration);
  }
}

// ---------------------------------------------------------------------------
// Rendering Telemetry & State
// ---------------------------------------------------------------------------
function renderReading(reading, isLive) {
  if (!reading) return;

  const temp = reading.temp !== undefined ? reading.temp : null;
  const hum = reading.humidity !== undefined ? reading.humidity : null;
  const mq2 = reading.mq2 !== undefined ? reading.mq2 : null;
  const delta = reading.delta !== undefined ? reading.delta : null;
  const tempRate = reading.temp_rate !== undefined ? reading.temp_rate : 0.0;
  const mq2Rate = reading.mq2_rate !== undefined ? reading.mq2_rate : 0.0;
  const prediction = reading.prediction || "NOT FIRE";
  const confidence = reading.confidence !== undefined ? reading.confidence : 100;
  const isSuppressed = !!reading.safety_suppressed;

  // Format time
  const timeLabel = reading.timestamp
    ? new Date(reading.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

  // Update Metric Cards
  const elTemp = document.getElementById("valTemp");
  const elHum = document.getElementById("valHum");
  const elMQ2 = document.getElementById("valMQ2");
  const elDelta = document.getElementById("valDelta");
  const elTempRate = document.getElementById("valTempRate");
  const elMQ2Rate = document.getElementById("valMQ2Rate");

  if (elTemp && temp !== null) elTemp.textContent = temp.toFixed(1);
  if (elHum && hum !== null) elHum.textContent = hum.toFixed(1);
  if (elMQ2 && mq2 !== null) elMQ2.textContent = Math.round(mq2);
  if (elDelta && delta !== null) elDelta.textContent = `Δ: ${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`;

  if (elTempRate) elTempRate.textContent = `Rate: ${tempRate >= 0 ? "+" : ""}${tempRate.toFixed(2)}°C/s`;
  if (elMQ2Rate) elMQ2Rate.textContent = `Rate: ${mq2Rate >= 0 ? "+" : ""}${mq2Rate.toFixed(2)}/s`;

  // Update LoRa Sensor Node ID under verdict box
  const nodeId = reading.node_id || "ESP32-LORA-NODE-01";
  const elNodeId = document.getElementById("sensorNodeId");
  if (elNodeId) {
    elNodeId.textContent = nodeId;
  }

  // Update Master Status Card
  updateMasterStatus(prediction, isSuppressed, confidence, timeLabel);

  // Push to Chart
  if (temp !== null && mq2 !== null) {
    pushChartPoint(timeLabel, temp, mq2);
  }

  // Prepend to Events Table if new live event
  if (isLive) {
    prependTableRow(reading, timeLabel);
  }
}

function updateMasterStatus(prediction, isSuppressed, confidence, timeStr) {
  const card = document.getElementById("masterStatusCard");
  const headline = document.getElementById("statusHeadline");
  const subtext = document.getElementById("statusSubtext");
  const confText = document.getElementById("statusConfidence");
  const syncText = document.getElementById("statusLastSync");
  const icon = document.getElementById("statusIcon");

  if (confText) confText.textContent = `${confidence}%`;
  if (syncText) syncText.textContent = timeStr || "Just now";

  if (!card || !headline) return;

  if (prediction === "FIRE" && !isSuppressed) {
    card.className = "card status-card status-fire";
    headline.textContent = "🔥 FIRE DETECTED";
    if (subtext) subtext.textContent = "CRITICAL ALERT: Flame / Smoke signatures exceed threshold. Twilio SMS dispatched.";
    if (icon) icon.setAttribute("data-lucide", "alert-triangle");
  } else if (isSuppressed) {
    card.className = "card status-card status-suppressed";
    headline.textContent = "SAFETY SUPPRESSED";
    if (subtext) subtext.textContent = "Model flagged FIRE, but MQ-2 is within safe calibrated baseline. Suppressed as likely false positive.";
    if (icon) icon.setAttribute("data-lucide", "alert-circle");
  } else {
    card.className = "card status-card status-safe";
    headline.textContent = "NOT FIRE";
    if (subtext) subtext.textContent = "All telemetry within calibrated baseline thresholds. Sensor network nominal.";
    if (icon) icon.setAttribute("data-lucide", "shield-check");
  }

  initLucide();
}

function markLatestEventSuppressed() {
  // Update Master Status card to Suppressed
  const headline = document.getElementById("statusHeadline");
  const subtext = document.getElementById("statusSubtext");
  const card = document.getElementById("masterStatusCard");
  const icon = document.getElementById("statusIcon");

  if (card) card.className = "card status-card status-suppressed";
  if (headline) headline.textContent = "SAFETY SUPPRESSED";
  if (subtext) subtext.textContent = "Model flagged FIRE, but MQ-2 is within safe calibrated baseline. Suppressed as likely false positive.";
  if (icon) icon.setAttribute("data-lucide", "alert-circle");
  initLucide();

  // Update top row of table
  const firstRow = document.querySelector("#eventsTableBody tr:not(.empty-row)");
  if (firstRow) {
    firstRow.className = "row-suppressed";
    const noteCell = firstRow.cells[9];
    if (noteCell) {
      noteCell.innerHTML = `<span class="badge-suppressed">└─ [SAFETY] Suppressed (Baseline Safe)</span>`;
    }
  }
}

// ---------------------------------------------------------------------------
// Phase & Warm-up Banner
// ---------------------------------------------------------------------------
function renderPhase(phase, details, secondsRemaining) {
  const banner = document.getElementById("phaseBanner");
  const title = document.getElementById("phaseTitle");
  const desc = document.getElementById("phaseDesc");
  const bar = document.getElementById("phaseProgressBar");
  const phaseStat = document.getElementById("statusPhaseName");

  if (phaseStat) {
    phaseStat.textContent = phase.charAt(0).toUpperCase() + phase.slice(1);
  }

  if (!banner) return; // Banner was removed by user request

  if (phase === "warmup") {
    banner.classList.remove("hidden");
    if (title) title.textContent = "Sensor Warming Up";
    if (desc) desc.textContent = details || `MQ-2 heater stabilizing (${secondsRemaining || 0}s remaining)`;
    const pct = Math.max(5, Math.min(100, Math.round(((60 - (secondsRemaining || 0)) / 60) * 100)));
    if (bar) bar.style.width = `${pct}%`;
  } else if (phase === "calibrating") {
    banner.classList.remove("hidden");
    if (title) title.textContent = "Calibrating Environment Baseline";
    if (desc) desc.textContent = details || `Establishing clean air baseline (${secondsRemaining || 0}s remaining)`;
    const pct = Math.max(5, Math.min(100, Math.round(((120 - (secondsRemaining || 0)) / 120) * 100)));
    if (bar) bar.style.width = `${pct}%`;
  } else {
    // Monitoring phase
    banner.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------------
// Calibration Rendering (with null checks)
// ---------------------------------------------------------------------------
function renderCalibration(cal) {
  if (!cal) return;

  const elBaseMQ2 = document.getElementById("calBaseMQ2");
  const elStdDev = document.getElementById("calStdDev");
  const elDelta = document.getElementById("calThresholdDelta");
  const elBaseTemp = document.getElementById("calBaseTemp");
  const elNoteBaseTemp = document.getElementById("noteBaseTemp");
  const elTrigger = document.getElementById("calTriggerLevel");
  const elTime = document.getElementById("calTime");

  if (elBaseMQ2 && cal.baseline_mq2 !== null && cal.baseline_mq2 !== undefined) {
    elBaseMQ2.textContent = cal.baseline_mq2.toFixed(1);
  }
  if (elStdDev && cal.stddev !== null && cal.stddev !== undefined) {
    elStdDev.textContent = cal.stddev.toFixed(2);
  }
  if (elDelta && cal.threshold_delta !== null && cal.threshold_delta !== undefined) {
    elDelta.textContent = `+${cal.threshold_delta.toFixed(1)}`;
  }
  if (cal.baseline_temp !== null && cal.baseline_temp !== undefined) {
    if (elBaseTemp) elBaseTemp.textContent = `${cal.baseline_temp.toFixed(1)} °C`;
    if (elNoteBaseTemp) elNoteBaseTemp.textContent = `Base: ${cal.baseline_temp.toFixed(1)}°C`;
  }

  // Trigger level calculation
  if (elTrigger && cal.baseline_mq2 !== undefined && cal.threshold_delta !== undefined) {
    const trigger = cal.baseline_mq2 + cal.threshold_delta;
    elTrigger.textContent = `≥ ${trigger.toFixed(1)} ADC`;
  }

  if (elTime && cal.calibrated_at) {
    const dt = new Date(cal.calibrated_at);
    elTime.textContent = dt.toLocaleTimeString();
  }
}

// ---------------------------------------------------------------------------
// Table Prepending & Historical Loading
// ---------------------------------------------------------------------------
function prependTableRow(event, timeStr) {
  const tbody = document.getElementById("eventsTableBody");
  const emptyRow = tbody.querySelector(".empty-row");
  if (emptyRow) emptyRow.remove();

  const tr = document.createElement("tr");

  // Determine row class and badge
  let predBadge = "";
  let safetyNote = '<span class="text-muted">—</span>';

  if (event.prediction === "FIRE" && !event.safety_suppressed) {
    tr.className = "row-fire";
    predBadge = '<span class="badge-fire">🔥 FIRE</span>';
  } else if (event.safety_suppressed) {
    tr.className = "row-suppressed";
    predBadge = '<span class="badge-suppressed">FIRE (SUPPRESSED)</span>';
    safetyNote = '<span class="badge-suppressed">└─ [SAFETY] Baseline Check</span>';
  } else {
    predBadge = '<span class="badge-safe">NOT FIRE</span>';
  }

  tr.innerHTML = `
    <td class="text-mono">${timeStr}</td>
    <td>${predBadge}</td>
    <td><strong>${event.confidence}%</strong></td>
    <td class="text-mono">${event.mq2 ? Math.round(event.mq2) : '--'}</td>
    <td class="text-mono">${event.delta !== undefined ? (event.delta >= 0 ? "+" : "") + Number(event.delta).toFixed(1) : '--'}</td>
    <td class="text-mono">${event.temp !== undefined ? Number(event.temp).toFixed(1) : '--'}</td>
    <td class="text-mono">${event.humidity !== undefined ? Number(event.humidity).toFixed(1) + '%' : '--'}</td>
    <td class="text-mono">${event.mq2_rate !== undefined ? (event.mq2_rate >= 0 ? "+" : "") + Number(event.mq2_rate).toFixed(2) : '--'}</td>
    <td class="text-mono">${event.temp_rate !== undefined ? (event.temp_rate >= 0 ? "+" : "") + Number(event.temp_rate).toFixed(2) : '--'}</td>
    <td>${safetyNote}</td>
  `;

  tbody.insertBefore(tr, tbody.firstChild);

  // Keep table capped at 150 rows in DOM for smooth UI
  if (tbody.children.length > 150) {
    tbody.removeChild(tbody.lastChild);
  }
}

// ---------------------------------------------------------------------------
// Initial REST Data Load
// ---------------------------------------------------------------------------
async function fetchInitialData() {
  try {
    const [statusRes, eventsRes] = await Promise.all([
      fetch("/api/status"),
      fetch("/api/events?limit=35"),
    ]);

    if (statusRes.ok) {
      const statusData = await statusRes.json();
      if (statusData.mode) setSystemMode(statusData.mode);
      if (statusData.calibration) renderCalibration(statusData.calibration);
      if (statusData.phase) renderPhase(statusData.phase, statusData.phase_details, statusData.seconds_remaining);
      if (statusData.latest_reading) renderReading(statusData.latest_reading, false);
    }

    if (eventsRes.ok) {
      const eventsData = await eventsRes.json();
      const tbody = document.getElementById("eventsTableBody");
      tbody.innerHTML = ""; // clear previous rows

      if (eventsData.events && eventsData.events.length > 0) {
        // Table needs newest first (which is how the API returns them)
        eventsData.events.forEach((ev) => {
          const tStr = ev.timestamp
            ? new Date(ev.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
            : "--:--:--";
          prependTableRow(ev, tStr);
        });

        // Chart needs oldest to newest
        const chartEvents = [...eventsData.events].reverse();
        chartLabels.length = 0;
        chartTempData.length = 0;
        chartMQ2Data.length = 0;

        chartEvents.forEach((ev) => {
          const tStr = ev.timestamp
            ? new Date(ev.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
            : "";
          if (ev.temp !== null && ev.mq2 !== null) {
            chartLabels.push(tStr);
            chartTempData.push(ev.temp);
            chartMQ2Data.push(ev.mq2);
          }
        });

        if (telemetryChart) telemetryChart.update();
      } else {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="10">No telemetry recorded yet. ESP32 serial lines will stream here.</td></tr>`;
      }
    }
  } catch (err) {
    console.error("Error fetching initial status/events:", err);
  }
}

function setSystemMode(mode) {
  const badge = document.getElementById("systemModeBadge");
  const text = document.getElementById("systemModeText");
  if (mode === "mock") {
    text.textContent = "Demo Mock Replay";
    badge.className = "badge badge-neutral";
    badge.style.borderColor = "rgba(168, 85, 247, 0.4)";
    badge.style.color = "#c084fc";
  } else {
    text.textContent = "Hardware Serial";
    badge.className = "badge badge-neutral";
  }
}

// ---------------------------------------------------------------------------
// Event Listeners & Interactive Controls
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// SMS Recipients Management
// ---------------------------------------------------------------------------
async function fetchRecipients() {
  try {
    const res = await fetch("/api/recipients");
    if (!res.ok) return;
    const data = await res.json();
    renderRecipients(data.recipients || []);
    updateNavPhoneBadge(data);
    updateTwilioGatewayBadge(data.twilio_configured);
  } catch (err) {
    console.error("Error fetching recipients:", err);
  }
}

function updateNavPhoneBadge(data) {
  const badge = document.getElementById("navPhoneBadge");
  const text = document.getElementById("navPhoneText");
  if (!text) return;

  const count = (data.recipients || []).length;
  const primary = data.primary;

  if (count > 0 && primary) {
    const shortNum = primary.slice(-10);
    text.textContent = `SMS: +..${shortNum.slice(-7)} (+${count})`;
    if (badge) {
      badge.style.borderColor = "rgba(56,189,248,0.4)";
      badge.style.color = "#38bdf8";
    }
  } else {
    text.textContent = "SMS: No number registered";
    if (badge) {
      badge.style.borderColor = "";
      badge.style.color = "";
    }
  }
}

function updateTwilioGatewayBadge(isConfigured) {
  const badge = document.getElementById("twilioGatewayBadge");
  const text = document.getElementById("twilioGatewayText");
  if (!badge || !text) return;

  if (isConfigured) {
    badge.className = "sms-status-indicator";
    text.textContent = "Twilio Gateway Armed";
  } else {
    badge.className = "sms-status-indicator sms-status-warning";
    text.textContent = "Twilio Pending (.env)";
  }
}

function renderRecipients(recipients) {
  const list = document.getElementById("recipientsList");
  const countEl = document.getElementById("recipientCount");
  if (!list) return;

  if (countEl) countEl.textContent = recipients.length;

  if (!recipients || recipients.length === 0) {
    list.innerHTML = '<div class="recipients-empty">No alert recipients registered yet. Enter a mobile number above.</div>';
    return;
  }

  list.innerHTML = "";
  recipients.forEach(r => {
    const chip = document.createElement("div");
    chip.className = "recipient-chip";
    chip.innerHTML = `
      <i data-lucide="phone-call" class="recipient-chip-icon"></i>
      <span class="recipient-phone">${r.phone_number}</span>
      <span class="recipient-name">${r.name || "Responder"}</span>
      <button class="recipient-del-btn" data-phone="${r.phone_number}" title="Remove recipient">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    `;
    chip.querySelector(".recipient-del-btn").addEventListener("click", () => deleteRecipient(r.phone_number));
    list.appendChild(chip);
  });

  initLucide();
}

async function deleteRecipient(phone) {
  try {
    const res = await fetch("/api/recipients", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone_number: phone }),
    });
    const data = await res.json();
    if (res.ok) {
      renderRecipients(data.recipients || []);
      updateNavPhoneBadge(data);
      showToast(`Removed ${phone} from SMS alert list.`, "warning");
      await fetchRecipients();
    }
  } catch (err) {
    showToast(`Failed to remove recipient: ${err}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Event Listeners & Interactive Controls
// ---------------------------------------------------------------------------
function setupEventListeners() {
  // Top-nav quick Test SMS button
  const btnTestSMS = document.getElementById("btnTestSMS");
  if (btnTestSMS) {
    btnTestSMS.addEventListener("click", async () => {
      btnTestSMS.disabled = true;
      btnTestSMS.innerHTML = '<span class="icon-sm">⏳</span> Sending...';

      try {
        const res = await fetch("/api/test-sms", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const result = await res.json();
        const type = result.status === "ok" ? "success" : result.status === "info" ? "warning" : "warning";
        showToast(result.message || "Test alert dispatched!", type);
      } catch (err) {
        showToast(`Failed to trigger test SMS: ${err}`, "error");
      } finally {
        btnTestSMS.disabled = false;
        btnTestSMS.innerHTML = '<i data-lucide="bell" class="icon-sm"></i><span>Trigger Alert</span>';
        initLucide();
      }
    });
  }

  // Phone registration form submit
  const phoneForm = document.getElementById("phoneRegisterForm");
  if (phoneForm) {
    phoneForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const phoneInput = document.getElementById("inputPhoneNumber");
      const nameInput = document.getElementById("inputResponderName");
      const btn = document.getElementById("btnRegisterPhone");

      const phone = phoneInput ? phoneInput.value.trim() : "";
      const name = nameInput ? nameInput.value.trim() : "";

      if (!phone) return;

      btn.disabled = true;
      btn.innerHTML = '<span>Registering...</span>';

      try {
        const res = await fetch("/api/recipients", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone_number: phone, name: name || "Emergency Responder" }),
        });
        const data = await res.json();

        if (res.ok) {
          renderRecipients(data.recipients || []);
          updateNavPhoneBadge(data);
          updateTwilioGatewayBadge(data.twilio_configured);
          if (phoneInput) phoneInput.value = "";
          if (nameInput) nameInput.value = "";
          showToast(data.message || `${phone} registered!`, data.twilio_configured ? "success" : "warning");
        } else {
          showToast(data.message || "Registration failed.", "error");
        }
      } catch (err) {
        showToast(`Error registering phone: ${err}`, "error");
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="user-plus" class="icon-sm"></i><span>Register Phone</span>';
        initLucide();
      }
    });
  }

  // In-card "Send Test Alert" button
  const btnTestSMSReg = document.getElementById("btnTestSMSReg");
  if (btnTestSMSReg) {
    btnTestSMSReg.addEventListener("click", async () => {
      btnTestSMSReg.disabled = true;
      btnTestSMSReg.innerHTML = '<span class="icon-sm">⏳</span> Sending...';

      try {
        const res = await fetch("/api/test-sms", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const result = await res.json();
        const type = result.status === "ok" ? "success" : "warning";
        showToast(result.message || "Test alert dispatched!", type);
      } catch (err) {
        showToast(`Failed to send test alert: ${err}`, "error");
      } finally {
        btnTestSMSReg.disabled = false;
        btnTestSMSReg.innerHTML = '<i data-lucide="send" class="icon-sm"></i><span>Send Test Alert</span>';
        initLucide();
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Toast Notification Utility
// ---------------------------------------------------------------------------
function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  const iconName = type === "success" ? "check-circle" : type === "warning" ? "alert-triangle" : "alert-octagon";
  toast.innerHTML = `
    <i data-lucide="${iconName}" class="icon-sm"></i>
    <span>${message}</span>
  `;

  container.appendChild(toast);
  initLucide();

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}
