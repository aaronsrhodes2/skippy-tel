/* One-click copy, everywhere on the portal (loaded by base.html on every page).

   Mark what people are expected to copy and this does the rest, including things that appear later (the join code
   that drops in by itself, chat replies):

     <code class="code">106969</code>       an invite code, an address, a password, a command: click it, or its Copy button
     <pre class="copy">...</pre>           a block to paste as-is (click it, or its Copy button)
     <pre data-copy-button>...</pre>       a block that is mostly read (logs): only its Copy button copies, so selecting text still works
     <button data-copy="text">Copy X</button>   any button or link that copies the text in data-copy (no extra button is added)

   Uses the clipboard API where the browser allows it and falls back to a hidden textarea (plain http, old browsers). If
   both are blocked the text is selected and the button says so, so a manual Ctrl+C still takes one keystroke. */
(function () {
  "use strict";

  var SHOWN = "code.code, pre.copy, pre[data-copy-button]";      // get a visible Copy button
  var CLICKABLE = "code.code, pre.copy, [data-copy]";            // copy when clicked
  var FLASH_MS = 1500;

  function valueOf(el) {
    var explicit = el.getAttribute("data-copy");
    return (explicit !== null ? explicit : el.textContent).trim();
  }

  function write(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var box = document.createElement("textarea");
      box.value = text;
      box.setAttribute("readonly", "");
      box.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(box);
      box.select();
      try { document.execCommand("copy") ? resolve() : reject(new Error("copy refused")); }
      catch (e) { reject(e); }
      finally { document.body.removeChild(box); }
    });
  }

  function selectText(el) {                                      // the last resort: leave it selected for Ctrl+C
    try {
      var range = document.createRange();
      range.selectNodeContents(el);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) { /* nothing more to do */ }
  }

  function say(label, text) {                                    // show feedback on a button, then put its own label back
    if (!label) return;
    if (label.__copyOriginal === undefined) label.__copyOriginal = label.textContent;
    label.textContent = text;
    clearTimeout(label.__copyTimer);
    label.__copyTimer = setTimeout(function () {
      label.textContent = label.__copyOriginal;
      label.__copyOriginal = undefined;
    }, FLASH_MS);
  }

  function copy(el, button) {
    var label = button || (el.tagName === "BUTTON" ? el : null);
    write(valueOf(el)).then(function () {
      say(label, "Copied ✓");
      el.classList.add("copied");
      setTimeout(function () { el.classList.remove("copied"); }, FLASH_MS);
    }, function () {
      if (el.hasAttribute("data-copy") && !el.matches(SHOWN)) window.prompt("Copy this text (Ctrl+C, Enter):", valueOf(el));
      else selectText(el);
      say(label, "Press Ctrl+C");
    });
  }

  function attach(el) {
    if (el.__copyButton || el.closest("a, button")) return;      // a button inside a link/button isn't valid HTML
    var button = document.createElement("button");
    button.type = "button";
    button.className = "copy-btn";
    button.textContent = "Copy";
    button.title = "Copy to the clipboard";
    button.__copyFor = el;
    el.__copyButton = button;
    el.insertAdjacentElement("afterend", button);
  }

  function enhance(root) {
    if (root.matches && root.matches(SHOWN)) attach(root);
    if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll(SHOWN), attach);
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var button = target.closest("button.copy-btn");
    if (button && button.__copyFor) { copy(button.__copyFor, button); return; }
    var el = target.closest(CLICKABLE);
    if (!el || el.matches("pre[data-copy-button]")) return;
    if (el.tagName === "A") event.preventDefault();
    copy(el, el.__copyButton || null);
  });

  function start() {
    enhance(document.body);
    if (window.MutationObserver) {
      new MutationObserver(function (changes) {
        changes.forEach(function (change) {
          Array.prototype.forEach.call(change.addedNodes, function (node) { if (node.nodeType === 1) enhance(node); });
        });
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.skippyCopy = { enhance: enhance, copy: write };         // for pages that build their own markup
})();
