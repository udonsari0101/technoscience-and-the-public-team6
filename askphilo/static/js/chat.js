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
  var dailyHint = document.querySelector("[data-daily]");
  var dailyRemaining = document.querySelector("[data-daily-remaining]");
  var avatarTpl = document.getElementById("tpl-assistant-avatar");
  var closeIconTpl = document.getElementById("tpl-close-icon");
  var fileInput = document.querySelector("[data-file-input]");
  var strip = document.querySelector("[data-attachments]");
  var attachError = document.querySelector("[data-attach-error]");
  var attachCost = document.querySelector("[data-attach-cost]");
  var sidebarList = document.querySelector("[data-sidebar-list]");
  var viewer = document.getElementById("photo-viewer");
  var viewerBody = document.querySelector("[data-photo-viewer]");
  var attachments = []; // {file, url, status: uploading|done, id, progress, node, xhr}
  var attachErrorTimer = null;
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
  var MSG_DAILY_DONE = "오늘 질문을 모두 사용했어요. 내일 0시에 다시 채워져요.";
  var MAX_PHOTOS = 5;
  var MAX_PHOTO_BYTES = 100 * 1024 * 1024;
  var MSG_TOO_MANY_PHOTOS = "사진은 한 번에 5장까지 보낼 수 있어요.";
  var MSG_PHOTO_TOO_BIG = "사진은 한 장에 100MB까지 보낼 수 있어요.";
  var MSG_NOT_PHOTO = "사진 파일만 보낼 수 있어요.";
  var MSG_UPLOAD_FAILED = "사진을 올리지 못했어요. 다시 시도해 주세요.";
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
  function textBubble(role, content) {
    var bubble = el("div", "bubble");
    bubble.appendChild(el("span", "sr-only", role === "user" ? "나: " : philosopherName + ": "));
    bubble.appendChild(document.createTextNode(content));
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
  function messageNode(role, content, index, photos) {
    var row = el("div", "msg msg-" + role);
    if (index != null) row.setAttribute("data-index", index);
    if (role === "assistant" && avatarTpl) row.appendChild(avatarTpl.content.cloneNode(true));
    if (photos && photos.length) {
      var body = el("div", "msg-body");
      body.appendChild(photosNode(photos));
      if (content) body.appendChild(textBubble(role, content));
      row.appendChild(body);
    } else {
      row.appendChild(textBubble(role, content));
    }
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
  function uploading() {
    return attachments.some(function (a) { return a.status === "uploading"; });
  }
  function updateSendState() {
    if (!sendBtn) return;
    var hasContent = (input && input.value.trim().length > 0) || attachments.length > 0;
    sendBtn.disabled = busy || !input || !hasContent || uploading();
    if (attachCost) {
      // each photo uses one more of today's questions
      attachCost.hidden = attachments.length === 0;
      attachCost.textContent = "· 사진 " + attachments.length + "장 포함 질문 " + (attachments.length + 1) + "개";
    }
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
  // Today's question allowance shown under the composer
  function applyDaily(daily) {
    if (!daily) return;
    if (dailyRemaining) dailyRemaining.textContent = String(daily.remaining);
    if (dailyHint) dailyHint.classList.toggle("is-low", !!daily.low);
    if (daily.exhausted) lockComposer();
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
      if (userRow && data.user_message) {
        userRow.setAttribute("data-index", data.user_message.index);
        swapPhotoUrls(userRow, data.user_message.images);
      }
      thread.appendChild(messageNode("assistant", data.assistant_message.content, data.assistant_message.index));
      clearAttachments();
      touchSidebar(conversationId, data.title || (document.querySelector("[data-chat-title]") || {}).textContent);
      applyStatus(data.status);
      applyDaily(data.daily);
      applyTitle(data.title);
    } else if (result.status === 401) {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      return;
    } else {
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
      } else {
        clearAttachments(); // stored with the message
        if (conversationId) touchSidebar(conversationId, null);
      }
      if (data.error === "in_progress") {
        thread.appendChild(pendingNode());
      } else if (data.error === "daily_limit") {
        thread.appendChild(errorNode(data.message || MSG_DAILY_DONE, false));
        applyDaily(data.daily);
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
    if (busy || uploading()) return;
    var content = (text || "").trim();
    var imageIds = attachments.map(function (a) { return a.id; });
    if (!content && !imageIds.length) return;
    removeAll("[data-starters]");
    removeAll("[data-pending]");
    var photos = attachments.map(function (a) { return { url: a.url, parts: [a.url] }; });
    var userRow = messageNode("user", content, null, photos);
    userRow.setAttribute("data-text", content);
    thread.appendChild(userRow);
    thread.appendChild(typingNode());
    scrollToBottom(true);
    if (input) {
      input.value = "";
      autoResize();
    }
    setBusy(true);
    var body = { content: content };
    if (imageIds.length) body.image_ids = imageIds;
    if (strip) strip.classList.add("is-sending");
    var request = conversationId
      ? post("/api/conversations/" + encodeURIComponent(conversationId) + "/messages", body)
      : post("/api/conversations", Object.assign({ philosopher_id: philosopherId }, body));
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
        if (strip) strip.classList.remove("is-sending");
        setBusy(false);
        if (input && desktopEnter) input.focus();
      });
  }

  // ------------------------------------------------------------- photos
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
    var form = new FormData();
    form.append("image", item.file, item.file.name || "photo.jpg");
    xhr.send(form);
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
          thread.insertBefore(messageNode(m.role, m.content, m.index, groupPhotos(m.images)), anchor);
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

  scrollToBottom(true);
  updateSendState();
})();
