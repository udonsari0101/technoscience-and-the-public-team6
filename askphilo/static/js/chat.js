// AskPhilo chat: optimistic user bubble → the reply streams in and is shown
// bubble by bubble (with a short pause between them, input locked meanwhile).
// No automatic retries: a failed reply keeps the user's message and offers Retry.
(function () {
  "use strict";

  var root = document.querySelector("[data-chat]");
  if (!root) return;

  var thread = document.getElementById("thread");
  var scroller = document.getElementById("chat-scroll");
  var form = document.querySelector("[data-composer]");
  var input = document.getElementById("composer-input");
  var sendBtn = form ? form.querySelector(".send-btn") : null;
  var limitNotice = document.querySelector("[data-limit-notice]");
  var gauge = document.querySelector("[data-daily]");
  var gaugeFill = document.querySelector("[data-daily-fill]");
  var gaugeCost = document.querySelector("[data-daily-cost]");
  var gaugeCostLabel = document.querySelector("[data-daily-cost-label]");
  var dailyRemaining = document.querySelector("[data-daily-remaining]");
  var avatarTpl = document.getElementById("tpl-assistant-avatar");
  var closeIconTpl = document.getElementById("tpl-close-icon");
  var infoIconTpl = document.getElementById("tpl-info-icon");
  var fileInput = document.querySelector("[data-file-input]");
  var strip = document.querySelector("[data-attachments]");
  var attachError = document.querySelector("[data-attach-error]");
  var sidebarList = document.querySelector("[data-sidebar-list]");
  var viewer = document.getElementById("photo-viewer");
  var viewerBody = document.querySelector("[data-photo-viewer]");
  var attachments = []; // {file, url, status: uploading|done, id, node, xhr}
  var attachErrorTimer = null;
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var conversationId = root.getAttribute("data-conversation-id") || "";
  var philosopherId = root.getAttribute("data-philosopher-id");
  var philosopherName = root.getAttribute("data-philosopher-name") || "상담";
  var oldestIndex = parseInt(root.getAttribute("data-oldest-index") || "0", 10);
  var busy = false;
  var desktopEnter = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  var daily = gauge ? {
    remaining: parseInt(gauge.getAttribute("data-remaining"), 10) || 0,
    limit: parseInt(gauge.getAttribute("data-limit"), 10) || 1
  } : null;

  var MSG_FAILED = "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.";
  var MSG_NETWORK = "잠시 연결이 불안정해요. 다시 시도해 주세요.";
  var MSG_PENDING = "답변을 준비하고 있어요. 잠시 후 새로고침해 주세요.";
  var MSG_DAILY_DONE = "오늘 질문을 모두 사용했어요. 내일 0시에 다시 채워져요.";
  var MAX_PHOTOS = 5;
  var MAX_PHOTO_BYTES = 100 * 1024 * 1024;
  var MSG_TOO_MANY_PHOTOS = "사진은 한 번에 5장까지 보낼 수 있어요.";
  var MSG_PHOTO_TOO_BIG = "사진은 한 장에 100MB까지 보낼 수 있어요.";
  var MSG_NOT_PHOTO = "사진 파일만 보낼 수 있어요.";
  var MSG_UPLOAD_FAILED = "사진을 올리지 못했어요. 다시 시도해 주세요.";
  var MSG_BASIS_ASK = "이 답변이 왜 이 철학자다운지 볼까요? 오늘 질문 1개를 써요.";
  var MSG_BASIS_LOADING = "근거를 정리하고 있어요…";
  // Errors raised before the user's message was stored: safe to put it back in the composer.
  var PRE_SAVE = {
    empty: 1, too_long: 1, too_large: 1, csrf: 1, rate_limited: 1, daily_limit: 1, busy: 1,
    bad_philosopher: 1, hard_limit: 1, in_progress: 1, storage: 1, bad_image: 1, too_many_images: 1
  };
  // Same bubble rule as askphilo/services/bubbles.py
  var MIN_BUBBLE_CHARS = 12;

  // ---------------------------------------------------------------- time (Seoul)
  var clockFmt = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "numeric", minute: "2-digit" });
  var dayFmt = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "long", day: "numeric", weekday: "long"
  });
  var keyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" });
  function nowInfo() {
    var d = new Date();
    return { time: clockFmt.format(d), day: keyFmt.format(d), day_label: dayFmt.format(d) };
  }

  // ---------------------------------------------------------------- helpers
  function nearBottom() {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  }
  function scrollToBottom(force) {
    if (force || nearBottom()) scroller.scrollTop = scroller.scrollHeight;
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text; // never innerHTML for message text
    return node;
  }
  function sentences(text) {
    return String(text || "").replace(/([.?!…~]+)\s+/g, "$1\n").split(/\n+/)
      .map(function (s) { return s.trim(); }).filter(Boolean);
  }
  function groupBubbles(parts) {
    var out = [], pending = "";
    parts.forEach(function (s) {
      pending = pending ? pending + " " + s : s;
      if (pending.length >= MIN_BUBBLE_CHARS) { out.push(pending); pending = ""; }
    });
    if (pending) out.push(pending);
    return out;
  }
  function removeAll(selector) {
    thread.querySelectorAll(selector).forEach(function (n) { n.remove(); });
  }

  // ---------------------------------------------------------------- nodes
  function bubbleNode(role, text, first) {
    var bubble = el("div", "bubble");
    if (first) bubble.appendChild(el("span", "sr-only", role === "user" ? "나: " : philosopherName + ": "));
    bubble.appendChild(document.createTextNode(text));
    return bubble;
  }
  // photos: [{url, parts: [url…]}] (one entry per photo, parts top to bottom)
  function photosNode(photos) {
    var box = el("div", "msg-photos" + (photos.length > 1 ? " is-grid" : ""));
    photos.forEach(function (photo, i) {
      var link = el("a", "msg-photo");
      link.href = photo.parts[0];
      link.target = "_blank";
      link.rel = "noopener";
      link.setAttribute("data-photo-parts", photo.parts.join(" "));
      link.setAttribute("aria-label", "보낸 사진 " + (i + 1) + " 크게 보기");
      var img = el("img");
      img.src = photo.url;
      img.alt = "";
      img.loading = "lazy";
      link.appendChild(img);
      box.appendChild(link);
    });
    return box;
  }
  function groupPhotos(images) {
    var groups = [];
    (images || []).forEach(function (image) {
      var last = groups[groups.length - 1];
      if (last && last.g === image.g) last.parts.push(image.url);
      else groups.push({ g: image.g, url: image.url, parts: [image.url] });
    });
    return groups;
  }
  function basisButton(ready) {
    var btn = el("button", "basis-btn");
    btn.type = "button";
    btn.setAttribute("data-basis", "");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-label", ready ? "이 답변의 근거 보기" : "이 답변의 근거 보기 (질문 1개 사용)");
    if (infoIconTpl) btn.appendChild(infoIconTpl.content.cloneNode(true));
    return btn;
  }
  function fillBasis(card, text) {
    card.replaceChildren();
    String(text || "").split("\n").forEach(function (line) {
      var p = el("p");
      var cut = line.indexOf(": ");
      if (cut > 0) {
        p.appendChild(el("strong", null, line.slice(0, cut)));
        p.appendChild(document.createTextNode(" " + line.slice(cut + 2)));
      } else {
        p.textContent = line;
      }
      card.appendChild(p);
    });
  }
  function basisCard(text) {
    var card = el("div", "basis-card");
    card.setAttribute("data-basis-card", "");
    card.hidden = true;
    if (text) {
      fillBasis(card, text);
      card.setAttribute("data-ready", "");
    }
    return card;
  }
  function metaNode(role, time, basis) {
    var meta = el("div", "msg-meta");
    var stamp = el("span", "msg-time", time || "");
    meta.appendChild(stamp);
    if (role === "assistant") meta.appendChild(basisButton(!!basis));
    return meta;
  }
  function setTime(row, time) {
    var stamp = row.querySelector(".msg-time");
    if (stamp && time) stamp.textContent = time;
  }
  function rowNode(role, index, day) {
    var row = el("div", "msg msg-" + role);
    if (index != null) row.setAttribute("data-index", index);
    if (day) row.setAttribute("data-day", day);
    if (role === "assistant" && avatarTpl) row.appendChild(avatarTpl.content.cloneNode(true));
    var body = el("div", "msg-body");
    row.appendChild(body);
    return { row: row, body: body };
  }
  // A whole message from the server (history pages, retries)
  function messageNode(m) {
    var parts = rowNode(m.role, m.index, m.day);
    var photos = groupPhotos(m.images);
    if (photos.length) parts.body.appendChild(photosNode(photos));
    if (m.role === "assistant") {
      (m.bubbles || groupBubbles(sentences(m.content))).forEach(function (b, i) {
        parts.body.appendChild(bubbleNode("assistant", b, i === 0));
      });
    } else if (m.content) {
      parts.body.appendChild(bubbleNode("user", m.content, true));
    }
    parts.body.appendChild(metaNode(m.role, m.time, m.basis));
    if (m.role === "assistant") parts.body.appendChild(basisCard(m.basis));
    return parts.row;
  }
  function typingNode(label) {
    var row = el("div", "msg msg-assistant");
    row.setAttribute("data-typing", "");
    if (avatarTpl) row.appendChild(avatarTpl.content.cloneNode(true));
    var bubble = el("div", "bubble typing");
    bubble.setAttribute("role", "status");
    bubble.appendChild(el("span", "sr-only", label || "답변을 준비하고 있어요"));
    bubble.appendChild(el("span"));
    bubble.appendChild(el("span"));
    bubble.appendChild(el("span"));
    if (label) bubble.appendChild(el("em", "typing-label", label));
    row.appendChild(bubble);
    return row;
  }
  function showTyping(label) {
    removeAll("[data-typing]");
    thread.appendChild(typingNode(label));
    scrollToBottom(true);
  }
  function pendingNode() {
    var row = el("div", "msg-pending");
    row.setAttribute("data-pending", "");
    row.setAttribute("role", "status");
    row.appendChild(el("span", null, MSG_PENDING));
    var link = el("a", "btn btn-secondary btn-sm", "새로고침");
    link.href = window.location.pathname;
    row.appendChild(link);
    return row;
  }
  function errorNode(message, retryable) {
    var row = el("div", "msg-error");
    row.setAttribute("data-pending", "");
    row.appendChild(el("span", null, message));
    if (retryable) {
      var btn = el("button", "btn btn-secondary", "다시 시도");
      btn.type = "button";
      btn.setAttribute("data-retry", "");
      row.appendChild(btn);
    }
    return row;
  }
  // Date line when the (Seoul) day changes
  function lastDay() {
    var rows = thread.querySelectorAll(".msg[data-day], .day-sep");
    var last = rows[rows.length - 1];
    return last ? last.getAttribute("data-day") : "";
  }
  function ensureDay(info) {
    if (!info || !info.day || info.day === lastDay()) return;
    var sep = el("div", "day-sep");
    sep.setAttribute("data-day", info.day);
    sep.setAttribute("role", "separator");
    sep.appendChild(el("span", null, info.day_label));
    thread.appendChild(sep);
  }
  function rebuildDays() {
    removeAll(".day-sep");
    var day = "";
    thread.querySelectorAll(".msg[data-day]").forEach(function (row) {
      var d = row.getAttribute("data-day");
      if (d && d !== day) {
        var sep = el("div", "day-sep");
        sep.setAttribute("data-day", d);
        sep.setAttribute("role", "separator");
        sep.appendChild(el("span", null, row.getAttribute("data-day-label") || d));
        row.parentNode.insertBefore(sep, row);
        day = d;
      }
    });
  }

  // ---------------------------------------------------------------- state
  function setBusy(value) {
    busy = value;
    root.classList.toggle("is-replying", value);
    if (sendBtn) {
      sendBtn.classList.toggle("is-loading", value);
      updateSendState();
    }
    if (input) {
      input.readOnly = value; // keeps the mobile keyboard open, blocks edits until the reply is done
      input.setAttribute("aria-busy", value ? "true" : "false");
    }
    if (fileInput) fileInput.disabled = value;
  }
  function uploading() {
    return attachments.some(function (a) { return a.status === "uploading"; });
  }
  function updateSendState() {
    previewCost();
    if (!sendBtn) return;
    var hasContent = (input && input.value.trim().length > 0) || attachments.length > 0;
    sendBtn.disabled = busy || !input || !hasContent || uploading();
  }
  function autoResize() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.max(44, Math.min(input.scrollHeight, 160)) + "px";
  }
  function applyStatus(status) {
    if (!status) return;
    if (status.hard_limit_reached) {
      window.location.reload(); // server renders the locked composer
      return;
    }
    if (limitNotice && status.notice) {
      limitNotice.firstChild.textContent = status.notice + " ";
      limitNotice.hidden = false;
    }
  }

  // ---------------------------------------------------------------- daily gauge
  function renderGauge() {
    if (!gauge || !daily) return;
    if (gaugeFill) gaugeFill.style.width = (daily.remaining / daily.limit * 100) + "%";
    if (dailyRemaining) dailyRemaining.textContent = String(daily.remaining);
    gauge.classList.toggle("is-low", daily.remaining <= Math.max(1, daily.limit * 0.1));
    previewCost();
  }
  // Photos waiting to be sent: show what this send will use (one per photo + the message).
  function previewCost() {
    if (!gauge || !daily || !gaugeCost) return;
    var cost = attachments.length ? Math.min(daily.remaining, attachments.length + 1) : 0;
    gaugeCost.style.width = (cost / daily.limit * 100) + "%";
    gaugeCost.style.left = ((daily.remaining - cost) / daily.limit * 100) + "%";
    if (gaugeCostLabel) {
      gaugeCostLabel.hidden = !cost;
      gaugeCostLabel.textContent = "−" + cost;
    }
  }
  function floatDrop(n) {
    if (!gauge || n <= 0) return;
    var chip = el("span", "daily-gauge-float", "−" + n);
    gauge.appendChild(chip);
    setTimeout(function () { chip.remove(); }, 1400);
  }
  function spend(n) { // optimistic: the server reserves the same amount right away
    if (!daily) return;
    daily.remaining = Math.max(0, daily.remaining - n);
    renderGauge();
    floatDrop(n);
  }
  function applyDaily(server) {
    if (!server || !daily) return;
    daily.remaining = server.remaining;
    daily.limit = server.limit;
    renderGauge();
    if (server.exhausted) lockComposer();
  }
  function lockComposer() {
    var composer = document.querySelector(".composer");
    if (!composer || composer.querySelector(".composer-locked")) return;
    var box = el("div", "composer-locked");
    box.setAttribute("role", "status");
    box.appendChild(el("p", null, MSG_DAILY_DONE));
    composer.replaceChildren(box);
    form = null;
    input = null;
    sendBtn = null;
  }

  // ---------------------------------------------------------------- title / sidebar
  function applyTitle(title) {
    if (!title) return;
    var h1 = document.querySelector("[data-chat-title]");
    var sub = document.querySelector("[data-chat-subtitle]");
    var renameInput = document.querySelector("[data-rename-input]");
    if (h1) h1.textContent = title;
    if (sub) {
      sub.textContent = philosopherName;
      sub.hidden = false;
    }
    var current = sidebarList && sidebarList.querySelector('[aria-current="page"] .conv-title');
    if (current) current.textContent = title;
    if (renameInput) renameInput.value = title;
    document.title = title + " · AskPhilo";
  }
  // Desktop sidebar: the active conversation moves to the top as soon as it changes,
  // a new one appears right away (no reload needed).
  function touchSidebar(id, title) {
    if (!sidebarList || !id) return;
    var empty = sidebarList.querySelector("[data-sidebar-empty]");
    if (empty) empty.remove();
    var href = "/c/" + encodeURIComponent(id);
    var link = sidebarList.querySelector('a.conv-link[href="' + href + '"]');
    var item = link ? link.closest("li") : null;
    if (!item) {
      item = el("li", "conv-item");
      link = el("a", "conv-link");
      link.href = href;
      if (avatarTpl) link.appendChild(avatarTpl.content.cloneNode(true));
      var body = el("span", "conv-body");
      body.appendChild(el("span", "conv-title", title || "새 상담"));
      body.appendChild(el("span", "conv-sub", philosopherName + " · 방금 전"));
      link.appendChild(body);
      item.appendChild(link);
    } else {
      var sub = item.querySelector(".conv-sub");
      if (sub) sub.textContent = philosopherName + " · 방금 전";
      if (title) item.querySelector(".conv-title").textContent = title;
    }
    sidebarList.querySelectorAll('[aria-current="page"]').forEach(function (a) { a.removeAttribute("aria-current"); });
    link.setAttribute("aria-current", "page");
    sidebarList.insertBefore(item, sidebarList.firstChild);
  }
  function adoptConversation(id) {
    if (!id || conversationId) return;
    conversationId = id;
    root.setAttribute("data-conversation-id", id);
    // Rename / delete become available once the conversation exists.
    document.querySelectorAll("[data-conversation-link]").forEach(function (a) {
      a.href = a.getAttribute("data-conversation-link").replace("{id}", encodeURIComponent(id));
      a.hidden = false;
    });
    document.querySelectorAll("[data-conversation-action]").forEach(function (f) {
      f.action = f.getAttribute("data-conversation-action").replace("{id}", encodeURIComponent(id));
    });
    if (window.history && history.replaceState) {
      history.replaceState(null, "", "/c/" + encodeURIComponent(id));
    }
  }

  // ---------------------------------------------------------------- requests
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { status: res.status, data: data };
      });
    });
  }
  // POST that answers with server-sent events (or plain JSON when refused early).
  function streamPost(url, body, on) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Accept": "text/event-stream" },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      var type = res.headers.get("Content-Type") || "";
      if (type.indexOf("text/event-stream") !== 0) {
        return res.json().catch(function () { return {}; }).then(function (data) { on.json(res.status, data); });
      }
      var buffer = "";
      function dispatch(block) {
        var kind = "message", data = "";
        block.split("\n").forEach(function (line) {
          if (line.indexOf("event:") === 0) kind = line.slice(6).trim();
          else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
        });
        if (!data) return;
        try { on.event(kind, JSON.parse(data)); } catch (e) { /* ignore a broken event */ }
      }
      function drain(final) {
        var blocks = buffer.split("\n\n");
        buffer = final ? "" : blocks.pop();
        blocks.forEach(dispatch);
      }
      if (!res.body || !res.body.getReader) {
        return res.text().then(function (text) { buffer = text; drain(true); });
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      function read() {
        return reader.read().then(function (chunk) {
          if (chunk.done) {
            buffer += decoder.decode();
            drain(true);
            return;
          }
          buffer += decoder.decode(chunk.value, { stream: true });
          drain(false);
          return read();
        });
      }
      return read();
    });
  }

  // ---------------------------------------------------------------- reply player
  // Shows a reply bubble by bubble: the first one as soon as it is ready, then a
  // short "typing" pause before each next one. Calls onDone when everything is shown.
  function gapFor(text) {
    return Math.max(800, Math.min(1700, 500 + text.length * 20));
  }
  function createPlayer(onDone) {
    var p = { queue: [], pending: "", shown: [], final: null, row: null, body: null,
              timer: null, lastShown: 0, cancelled: false };
    function showNext() {
      removeAll("[data-typing]");
      var text = p.queue.shift();
      if (!p.row) {
        var parts = rowNode("assistant", null, null);
        p.row = parts.row;
        p.body = parts.body;
        thread.appendChild(p.row);
      }
      p.body.appendChild(bubbleNode("assistant", text, p.shown.length === 0));
      p.shown.push(text);
      p.lastShown = Date.now();
      if (p.queue.length || p.final === null) thread.appendChild(typingNode());
      scrollToBottom(true);
    }
    function finish() {
      removeAll("[data-typing]");
      var m = p.final;
      var expected = m.bubbles || groupBubbles(sentences(m.content));
      if (!p.row || expected.join("\n") !== p.shown.join("\n")) {
        // what was stored differs from what streamed (rare): show the stored text
        if (p.row) p.row.remove();
        ensureDay(m);
        thread.appendChild(messageNode(m));
      } else {
        p.row.setAttribute("data-index", m.index);
        if (m.day) p.row.setAttribute("data-day", m.day);
        p.body.appendChild(metaNode("assistant", m.time, m.basis));
        p.body.appendChild(basisCard(m.basis));
      }
      scrollToBottom(true);
      onDone();
    }
    function pump() {
      if (p.timer || p.cancelled) return;
      if (!p.queue.length) {
        if (p.final !== null) finish();
        return;
      }
      var wait = p.shown.length === 0 ? 0 : Math.max(0, gapFor(p.queue[0]) - (Date.now() - p.lastShown));
      p.timer = setTimeout(function () {
        p.timer = null;
        if (p.cancelled) return;
        showNext();
        pump();
      }, wait);
    }
    return {
      push: function (sentence) {
        p.pending = p.pending ? p.pending + " " + sentence : sentence;
        if (p.pending.length >= MIN_BUBBLE_CHARS) {
          p.queue.push(p.pending);
          p.pending = "";
          pump();
        }
      },
      end: function (message) {
        if (p.pending) {
          p.queue.push(p.pending);
          p.pending = "";
        }
        p.final = message;
        pump();
      },
      cancel: function () {
        p.cancelled = true;
        clearTimeout(p.timer);
        removeAll("[data-typing]");
      },
      started: function () { return !!p.row; },
    };
  }
  function playWhole(message, onDone) {
    var player = createPlayer(onDone);
    sentences(message.content).forEach(player.push);
    player.end(message);
  }

  // ---------------------------------------------------------------- sending
  function handleFailure(status, data, userRow, spent) {
    removeAll("[data-typing]");
    data = data || {};
    if (data.conversation_id) adoptConversation(data.conversation_id);
    if (status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      return;
    }
    if (data.error === "hard_limit" || data.error === "not_found") {
      window.location.reload();
      return;
    }
    if (userRow && PRE_SAVE[data.error]) {
      // Rejected before it was stored: give the text back (photos stay attached).
      userRow.remove();
      var sentText = userRow.getAttribute("data-text") || "";
      if (input && !input.value && sentText) {
        input.value = sentText;
        autoResize();
      }
      if (daily && spent) { daily.remaining += spent; renderGauge(); }
    } else {
      clearAttachments(); // stored with the message
      if (conversationId) touchSidebar(conversationId, null);
    }
    if (data.daily) applyDaily(data.daily);
    if (data.error === "in_progress") {
      thread.appendChild(pendingNode());
    } else if (data.error === "daily_limit") {
      thread.appendChild(errorNode(data.message || MSG_DAILY_DONE, false));
    } else if (data.error) {
      thread.appendChild(errorNode(data.message || MSG_FAILED, data.retryable === true));
    } else {
      // Proxy error / non-JSON: the message may or may not have been stored.
      thread.appendChild(errorNode(MSG_NETWORK, !!conversationId));
    }
    scrollToBottom(true);
  }
  function settle(data) {
    clearAttachments();
    touchSidebar(conversationId, data.title || (document.querySelector("[data-chat-title]") || {}).textContent);
    applyStatus(data.status);
    applyDaily(data.daily);
    applyTitle(data.title);
  }

  function send(text) {
    if (busy || uploading()) return;
    var content = (text || "").trim();
    var imageIds = attachments.map(function (a) { return a.id; });
    if (!content && !imageIds.length) return;
    removeAll("[data-starters]");
    removeAll("[data-pending]");
    var info = nowInfo();
    ensureDay(info);
    var parts = rowNode("user", null, info.day);
    var photos = attachments.map(function (a) { return { url: a.url, parts: [a.url] }; });
    if (photos.length) parts.body.appendChild(photosNode(photos));
    if (content) parts.body.appendChild(bubbleNode("user", content, true));
    parts.body.appendChild(metaNode("user", info.time));
    var userRow = parts.row;
    userRow.setAttribute("data-text", content);
    thread.appendChild(userRow);
    showTyping(imageIds.length ? "사진 보는 중" : null);
    if (input) {
      input.value = "";
      autoResize();
    }
    var cost = 1 + imageIds.length;
    setBusy(true);
    spend(cost);
    if (strip) strip.classList.add("is-sending");
    var body = { content: content };
    if (imageIds.length) body.image_ids = imageIds;
    var url = conversationId
      ? "/api/conversations/" + encodeURIComponent(conversationId) + "/messages/stream"
      : "/api/conversations/stream";
    if (!conversationId) body.philosopher_id = philosopherId;

    var player = null;
    var finished = false;
    function done() {
      if (strip) strip.classList.remove("is-sending");
      setBusy(false);
      if (input && desktopEnter) input.focus();
    }
    streamPost(url, body, {
      json: function (status, data) {
        finished = true;
        handleFailure(status, data, userRow, cost);
        done();
      },
      event: function (kind, data) {
        if (kind === "meta" || kind === "user") adoptConversation(data.conversation_id);
        if (kind === "user" && data.user_message) {
          userRow.setAttribute("data-index", data.user_message.index);
          setTime(userRow, data.user_message.time);
          swapPhotoUrls(userRow, data.user_message.images);
          showTyping(null);
        } else if (kind === "status" && data.stage === "reading_photos") {
          showTyping("사진 보는 중");
        } else if (kind === "sentence") {
          if (!player) player = createPlayer(function () { settle(player.result); done(); });
          player.push(data.text);
        } else if (kind === "done") {
          finished = true;
          if (!player) player = createPlayer(function () { settle(player.result); done(); });
          player.result = data;
          if (!player.started()) sentences(data.assistant_message.content).forEach(player.push);
          player.end(data.assistant_message);
        } else if (kind === "error") {
          finished = true;
          if (player) player.cancel();
          handleFailure(data.http_status || 500, data, player && player.started() ? null : userRow, cost);
          done();
        }
      },
    }).then(function () {
      if (!finished) {
        if (player) player.cancel();
        handleFailure(0, {}, null, 0);
        done();
      }
    }).catch(function () {
      if (finished) return;
      if (player) player.cancel();
      // Unknown whether the server stored the message: offer an explicit retry
      // only when we know the conversation exists.
      removeAll("[data-typing]");
      thread.appendChild(errorNode(MSG_NETWORK, !!conversationId));
      scrollToBottom(true);
      done();
    });
  }

  function retry() {
    if (busy || !conversationId) return;
    removeAll("[data-pending]");
    showTyping(null);
    setBusy(true);
    spend(1);
    post("/api/conversations/" + encodeURIComponent(conversationId) + "/retry")
      .then(function (result) {
        var data = result.data || {};
        if (data.error === "nothing_to_retry" || data.error === "conflict") {
          window.location.reload();
          return;
        }
        if (result.status === 200 && data.ok) {
          playWhole(data.assistant_message, function () { settle(data); setBusy(false); });
          return;
        }
        handleFailure(result.status, data, null, 1);
        if (PRE_SAVE[data.error] && daily) { daily.remaining += 1; renderGauge(); }
        setBusy(false);
      })
      .catch(function () {
        removeAll("[data-typing]");
        thread.appendChild(errorNode(MSG_NETWORK, true));
        setBusy(false);
      });
  }

  // ---------------------------------------------------------------- info button
  function toggleBasis(btn) {
    var row = btn.closest(".msg");
    var card = row && row.querySelector("[data-basis-card]");
    if (!card) return;
    if (!card.hidden) {
      card.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      return;
    }
    if (!card.hasAttribute("data-ready")) {
      card.replaceChildren();
      card.appendChild(el("p", "basis-ask", MSG_BASIS_ASK));
      var actions = el("div", "basis-actions");
      var go = el("button", "btn btn-secondary btn-sm", "보기");
      go.type = "button";
      go.setAttribute("data-basis-go", "");
      var cancel = el("button", "btn btn-ghost btn-sm", "취소");
      cancel.type = "button";
      cancel.setAttribute("data-basis-cancel", "");
      actions.appendChild(go);
      actions.appendChild(cancel);
      card.appendChild(actions);
    }
    card.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    scrollToBottom(false);
  }
  function loadBasis(go) {
    var row = go.closest(".msg");
    var card = row.querySelector("[data-basis-card]");
    var btn = row.querySelector("[data-basis]");
    var index = row.getAttribute("data-index");
    if (!conversationId || index == null) return;
    card.replaceChildren(el("p", "basis-ask", MSG_BASIS_LOADING));
    post("/api/conversations/" + encodeURIComponent(conversationId) + "/messages/" + index + "/basis")
      .then(function (result) {
        var data = result.data || {};
        if (result.status === 200 && data.ok) {
          fillBasis(card, data.basis);
          card.setAttribute("data-ready", "");
          if (btn) btn.setAttribute("aria-label", "이 답변의 근거 보기");
          if (data.charged && daily && data.daily) {
            var drop = daily.remaining - data.daily.remaining;
            applyDaily(data.daily);
            floatDrop(drop);
          }
        } else {
          card.replaceChildren(el("p", "basis-ask is-error", data.message || MSG_FAILED));
          if (data.daily) applyDaily(data.daily);
        }
      })
      .catch(function () {
        card.replaceChildren(el("p", "basis-ask is-error", MSG_NETWORK));
      });
  }

  // ---------------------------------------------------------------- photos
  function showAttachError(message) {
    if (!attachError) return;
    attachError.textContent = message;
    attachError.hidden = false;
    clearTimeout(attachErrorTimer);
    attachErrorTimer = setTimeout(function () { attachError.hidden = true; }, 4000);
  }
  function renderStrip() {
    if (!strip) return;
    strip.hidden = attachments.length === 0;
  }
  function attachmentNode(item) {
    var box = el("div", "attach-item is-uploading");
    var img = el("img");
    img.src = item.url;
    img.alt = "";
    box.appendChild(img);
    var bar = el("span", "attach-progress");
    bar.appendChild(el("span"));
    box.appendChild(bar);
    var remove = el("button", "attach-remove");
    remove.type = "button";
    remove.setAttribute("aria-label", "사진 빼기");
    if (closeIconTpl) remove.appendChild(closeIconTpl.content.cloneNode(true));
    remove.addEventListener("click", function () { removeAttachment(item); });
    box.appendChild(remove);
    return box;
  }
  function setProgress(item, ratio) {
    var fill = item.node.querySelector(".attach-progress span");
    if (fill) fill.style.width = Math.round(ratio * 100) + "%";
  }
  function upload(item) {
    var xhr = new XMLHttpRequest();
    item.xhr = xhr;
    xhr.open("POST", "/api/uploads");
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.addEventListener("progress", function (e) {
      if (e.lengthComputable) setProgress(item, e.loaded / e.total * 0.9); // last 10%: server work
    });
    xhr.addEventListener("load", function () {
      var data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* proxy error page */ }
      if (xhr.status === 201 && data.ok && data.upload) {
        item.status = "done";
        item.id = data.upload.id;
        item.node.classList.remove("is-uploading");
        setProgress(item, 1);
      } else if (xhr.status === 401) {
        window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
        return;
      } else {
        dropAttachment(item);
        showAttachError(data.message || MSG_UPLOAD_FAILED);
      }
      updateSendState();
    });
    xhr.addEventListener("error", function () {
      dropAttachment(item);
      showAttachError(MSG_UPLOAD_FAILED);
      updateSendState();
    });
    var data = new FormData();
    data.append("image", item.file, item.file.name || "photo.jpg");
    xhr.send(data);
  }
  function addFiles(files) {
    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      if (attachments.length >= MAX_PHOTOS) { showAttachError(MSG_TOO_MANY_PHOTOS); break; }
      if (file.type && file.type.indexOf("image/") !== 0) { showAttachError(MSG_NOT_PHOTO); continue; }
      if (file.size > MAX_PHOTO_BYTES) { showAttachError(MSG_PHOTO_TOO_BIG); continue; }
      var item = { file: file, url: URL.createObjectURL(file), status: "uploading", id: null };
      item.node = attachmentNode(item);
      strip.appendChild(item.node);
      attachments.push(item);
      upload(item);
    }
    renderStrip();
    updateSendState();
  }
  function dropAttachment(item) {
    attachments = attachments.filter(function (a) { return a !== item; });
    if (item.node) item.node.remove();
    URL.revokeObjectURL(item.url);
    renderStrip();
  }
  function removeAttachment(item) {
    if (busy) return;
    if (item.status === "uploading" && item.xhr) item.xhr.abort();
    if (item.id) post("/api/uploads/" + encodeURIComponent(item.id) + "/discard").catch(function () {});
    dropAttachment(item);
    updateSendState();
  }
  function clearAttachments() {
    // Sent: the bubble now shows the server copies, so the previews can go.
    attachments.forEach(function (a) { if (a.node) a.node.remove(); });
    attachments = [];
    renderStrip();
    updateSendState();
  }
  function swapPhotoUrls(row, images) {
    var groups = groupPhotos(images);
    row.querySelectorAll(".msg-photo").forEach(function (link, i) {
      var group = groups[i];
      if (!group) return;
      var img = link.querySelector("img");
      var old = img.src;
      img.src = group.url;
      link.href = group.parts[0];
      link.setAttribute("data-photo-parts", group.parts.join(" "));
      if (old.indexOf("blob:") === 0) setTimeout(function () { URL.revokeObjectURL(old); }, 1000);
    });
  }
  function openViewer(link) {
    if (!viewer || typeof viewer.showModal !== "function" || !viewerBody) return false;
    viewerBody.replaceChildren();
    (link.getAttribute("data-photo-parts") || link.href).split(" ").forEach(function (url) {
      var img = el("img");
      img.src = url;
      img.alt = "";
      viewerBody.appendChild(img);
    });
    viewer._trigger = link;
    viewer.showModal();
    viewerBody.scrollTop = 0;
    return true;
  }

  // ---------------------------------------------------------------- history
  function loadOlder(button) {
    if (!conversationId || button.disabled) return;
    button.disabled = true;
    fetch("/api/conversations/" + encodeURIComponent(conversationId) + "/messages?before=" + oldestIndex, {
      credentials: "same-origin",
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error("load failed");
        var prevHeight = scroller.scrollHeight;
        var anchor = button.nextSibling;
        data.messages.forEach(function (m) {
          var node = messageNode(m);
          if (m.day_label) node.setAttribute("data-day-label", m.day_label);
          thread.insertBefore(node, anchor);
        });
        rebuildDays();
        oldestIndex = data.oldest_index;
        if (!data.has_more) button.remove();
        else button.disabled = false;
        scroller.scrollTop += scroller.scrollHeight - prevHeight; // keep position
      })
      .catch(function () {
        button.disabled = false;
      });
  }

  // ---------------------------------------------------------------- wiring
  if (form && input) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      send(input.value);
    });
    input.addEventListener("input", function () {
      autoResize();
      updateSendState();
    });
    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey) return;
      // Never send while a Korean IME composition is in progress.
      if (event.isComposing || event.keyCode === 229) return;
      if (!desktopEnter) return; // on touch keyboards Enter inserts a newline
      event.preventDefault();
      send(input.value);
    });
  }

  if (fileInput && strip) {
    fileInput.addEventListener("change", function () {
      var files = Array.prototype.slice.call(fileInput.files || []);
      fileInput.value = ""; // the same photo can be picked again later
      addFiles(files);
    });
  }

  thread.addEventListener("click", function (event) {
    var photo = event.target.closest(".msg-photo");
    if (photo) {
      if (openViewer(photo)) event.preventDefault();
      return;
    }
    var basisBtn = event.target.closest("[data-basis]");
    if (basisBtn) {
      toggleBasis(basisBtn);
      return;
    }
    var go = event.target.closest("[data-basis-go]");
    if (go) {
      loadBasis(go);
      return;
    }
    var cancel = event.target.closest("[data-basis-cancel]");
    if (cancel) {
      var card = cancel.closest("[data-basis-card]");
      card.hidden = true;
      var owner = card.closest(".msg").querySelector("[data-basis]");
      if (owner) owner.setAttribute("aria-expanded", "false");
      return;
    }
    var starter = event.target.closest("[data-starter]");
    if (starter && input) {
      input.value = starter.textContent.trim();
      autoResize();
      updateSendState();
      input.focus();
      return;
    }
    if (event.target.closest("[data-retry]")) {
      retry();
      return;
    }
    var older = event.target.closest("[data-load-older]");
    if (older) loadOlder(older);
  });

  // Mobile keyboard: browsers that don't resize the layout (iOS Safari) only
  // shrink the visual viewport, so size the chat to it and keep the newest
  // message in view.
  if (window.visualViewport) {
    var vv = window.visualViewport;
    var lastHeight = vv.height;
    var fitViewport = function () {
      var h = vv.height;
      var html = document.documentElement;
      if (h < window.innerHeight - 1 && vv.scale <= 1.01) { // keyboard, not pinch zoom
        html.style.setProperty("--chat-h", h + "px");
        html.classList.add("kb-open");
        window.scrollTo(0, 0);
      } else {
        html.classList.remove("kb-open");
        html.style.removeProperty("--chat-h");
      }
      if (h < lastHeight) scrollToBottom(true);
      lastHeight = h;
    };
    vv.addEventListener("resize", fitViewport);
  }

  renderGauge();
  if (gauge) requestAnimationFrame(function () { gauge.classList.add("is-live"); });
  scrollToBottom(true);
  updateSendState();
})();
