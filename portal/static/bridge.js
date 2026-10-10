/* The bridge shell (templates/base.html, {% block ui %}bridge{% endblock %}): the one small script every bridge page loads.

   iOS Safari only draws :active (a control's backlight when pressed) if the page has a touch listener; this is that listener.
   It also keeps the screen from being dragged: the page is exactly the viewport, so a pull is never a scroll.
   Nothing here talks to the network. */
(function () {
  document.addEventListener("touchstart", function () {}, { passive: true });
  document.addEventListener("touchmove", function (e) {
    // allow a drag only inside something that is meant to scroll or be dragged (a field, a marked panel)
    var el = e.target.closest && e.target.closest("textarea, input, select, [data-pan]");
    if (!el) e.preventDefault();
  }, { passive: false });
})();
