// AskPhilo chat: optimistic user bubble → typing dots → reply. No automatic
// retries: a failed reply keeps the user's message and offers a Retry button.
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
  var avatarTpl = document.getElementById("tpl-assistant-avatar");
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var conversationId = root.getAttribute("data-conversation-id") || "";
  var philosopherId = root.getAttribute("data-philosopher-id");
  var philosopherName = root.getAttribute("data-philosopher-name") || "상담";
  var oldestIndex = parseInt(root.getAttribute("data-oldest-index") || "0", 10);
  var busy = false;
  var desktopEnter = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  var MSG_FAILED = "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.";
  var MSG_NETWORK = "잠시 연결이 불안정해요. 다시 시도해 주세요.";
  var MSG_PENDING = "답변을 준비하고 있어요. 잠시 후 새로고침해 주세요.";
  // Errors raised before the user's message was stored: safe to put it back in the composer.
  var PRE_SAVE = {
    empty: 1, too_long: 1, too_large: 1, csrf: 1, rate_limited: 1, daily_limit: 1, busy: 1,
    bad_philosopher: 1, hard_limit: 1, in_progress: 1, storage: 1
  };

  // ---------------------------------------------------------------- helpers
  function nearBottom() {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  }
  function scrollToBottom(force) {
    if (force || nearBottom()) {
      scroller.scrollTop = scroller.scrollHeight;
    }
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text; // never innerHTML for message text
    return node;
  }
  function messageNode(role, content, index) {
    var row = el("div", "msg msg-" + role);
    if (index != null) row.setAttribute("data-index", index);
    if (role === "assistant" && avatarTpl) row.appendChild(avatarTpl.content.cloneNode(true));
    var bubble = el("div", "bubble");
    bubble.appendChild(el("span", "sr-only", role === "user" ? "나: " : philosopherName + ": "));
    bubble.appendChild(document.createTextNode(content));
    row.appendChild(bubble);
    return row;
  }
  function typingNode() {
    var row = el("div", "msg msg-assistant");
    row.setAttribute("data-typing", "");
    if (avatarTpl) row.appendChild(avatarTpl.content.cloneNode(true));
    var bubble = el("div", "bubble typing");
    bubble.setAttribute("role", "status");
    bubble.appendChild(el("span", "sr-only", "답변을 준비하고 있어요"));
    bubble.appendChild(el("span"));
    bubble.appendChild(el("span"));
    bubble.appendChild(el("span"));
    row.appendChild(bubble);
    return row;
  }
  function removeAll(selector) {
    thread.querySelectorAll(selector).forEach(function (n) { n.remove(); });
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
  function setBusy(value) {
    busy = value;
    if (sendBtn) {
      sendBtn.classList.toggle("is-loading", value);
      updateSendState();
    }
    if (input) {
      input.readOnly = value; // keeps the mobile keyboard open, blocks edits while sending
      input.setAttribute("aria-busy", value ? "true" : "false");
    }
  }
  function updateSendState() {
    if (!sendBtn) return;
    sendBtn.disabled = busy || !input || input.value.trim().length === 0;
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
  function adoptConversation(id) {
    if (!id || conversationId) return;
    conversationId = id;
    root.setAttribute("data-conversation-id", id);
    if (window.history && history.replaceState) {
      history.replaceState(null, "", "/c/" + encodeURIComponent(id));
    }
  }

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

  // ------------------------------------------------------------- sending
  function handleResult(result, userRow) {
    removeAll("[data-typing]");
    var data = result.data || {};
    if (data.conversation_id) adoptConversation(data.conversation_id);
    if (result.status >= 200 && result.status < 300 && data.ok) {
      if (userRow && data.user_message) userRow.setAttribute("data-index", data.user_message.index);
      thread.appendChild(messageNode("assistant", data.assistant_message.content, data.assistant_message.index));
      applyStatus(data.status);
    } else if (result.status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      return;
    } else {
      if (data.error === "hard_limit" || data.error === "not_found") {
        window.location.reload();
        return;
      }
      if (userRow && PRE_SAVE[data.error]) {
        // Rejected before it was stored: give the text back instead of a ghost bubble.
        userRow.remove();
        if (input && !input.value) {
          input.value = userRow.querySelector(".bubble").lastChild.textContent;
          autoResize();
        }
      }
      if (data.error === "in_progress") {
        thread.appendChild(pendingNode());
      } else if (data.error) {
        thread.appendChild(errorNode(data.message || MSG_FAILED, data.retryable === true));
      } else {
        // Proxy error / non-JSON: the message may or may not have been stored.
        thread.appendChild(errorNode(MSG_NETWORK, !!conversationId));
      }
    }
    scrollToBottom(true);
  }

  function send(text) {
    if (busy) return;
    var content = (text || "").trim();
    if (!content) return;
    removeAll("[data-starters]");
    removeAll("[data-pending]");
    var userRow = messageNode("user", content);
    thread.appendChild(userRow);
    thread.appendChild(typingNode());
    scrollToBottom(true);
    if (input) {
      input.value = "";
      autoResize();
    }
    setBusy(true);
    var request = conversationId
      ? post("/api/conversations/" + encodeURIComponent(conversationId) + "/messages", { content: content })
      : post("/api/conversations", { philosopher_id: philosopherId, content: content });
    request
      .then(function (result) { handleResult(result, userRow); })
      .catch(function () {
        removeAll("[data-typing]");
        // Unknown whether the server stored the message: offer an explicit retry
        // only when we know the conversation exists.
        thread.appendChild(errorNode(MSG_NETWORK, !!conversationId));
        scrollToBottom(true);
      })
      .then(function () {
        setBusy(false);
        if (input && desktopEnter) input.focus();
      });
  }

  function retry() {
    if (busy || !conversationId) return;
    removeAll("[data-pending]");
    thread.appendChild(typingNode());
    scrollToBottom(true);
    setBusy(true);
    post("/api/conversations/" + encodeURIComponent(conversationId) + "/retry")
      .then(function (result) {
        if (result.data && (result.data.error === "nothing_to_retry" || result.data.error === "conflict")) {
          window.location.reload();
          return;
        }
        handleResult(result, null);
      })
      .catch(function () {
        removeAll("[data-typing]");
        thread.appendChild(errorNode(MSG_NETWORK, true));
      })
      .then(function () { setBusy(false); });
  }

  // ------------------------------------------------------------- history
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
          thread.insertBefore(messageNode(m.role, m.content, m.index), anchor);
        });
        oldestIndex = data.oldest_index;
        if (!data.has_more) button.remove();
        else button.disabled = false;
        scroller.scrollTop += scroller.scrollHeight - prevHeight; // keep position
      })
      .catch(function () {
        button.disabled = false;
      });
  }

  // -------------------------------------------------------------- wiring
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

  thread.addEventListener("click", function (event) {
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

  // Keep the newest message visible when the mobile keyboard resizes the view.
  if (window.visualViewport) {
    var lastHeight = window.visualViewport.height;
    window.visualViewport.addEventListener("resize", function () {
      var h = window.visualViewport.height;
      if (h < lastHeight) scrollToBottom(true);
      lastHeight = h;
    });
  }

  scrollToBottom(true);
  updateSendState();
})();
