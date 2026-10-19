// Topic filters (shareable as #science etc.) and a friendly "updated 20 minutes ago".
// The page works without this script: every story is simply shown.
(function () {
  var chips = Array.prototype.slice.call(document.querySelectorAll(".chip"));
  var cards = Array.prototype.slice.call(document.querySelectorAll(".card"));
  var days = Array.prototype.slice.call(document.querySelectorAll(".day"));
  var empty = days.length ? document.querySelector(".empty") : null;

  function apply(filter) {
    if (!chips.some(function (c) { return c.dataset.filter === filter; })) filter = "all";
    chips.forEach(function (c) { c.setAttribute("aria-pressed", String(c.dataset.filter === filter)); });
    cards.forEach(function (card) { card.hidden = filter !== "all" && card.dataset.category !== filter; });
    var anyVisible = false;
    days.forEach(function (day) {
      var visible = !!day.querySelector(".card:not([hidden])");
      day.hidden = !visible;
      anyVisible = anyVisible || visible;
    });
    if (empty) empty.hidden = anyVisible;
    return filter;
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var filter = apply(chip.dataset.filter);
      var url = location.pathname + location.search + (filter === "all" ? "" : "#" + filter);
      history.replaceState(null, "", url);
    });
  });
  apply(location.hash.slice(1) || "all");
  window.addEventListener("hashchange", function () { apply(location.hash.slice(1) || "all"); });

  var stamp = document.querySelector("time.updated");
  if (stamp) {
    var minutes = Math.round((Date.now() - new Date(stamp.getAttribute("datetime"))) / 60000);
    var text = null;
    if (minutes >= 0 && minutes < 1) text = "just now";
    else if (minutes >= 1 && minutes < 60) text = minutes + (minutes === 1 ? " minute ago" : " minutes ago");
    else if (minutes >= 60 && minutes < 24 * 60) {
      var hours = Math.round(minutes / 60);
      text = hours + (hours === 1 ? " hour ago" : " hours ago");
    }
    if (text) {
      stamp.title = stamp.textContent;
      stamp.textContent = text;
    }
  }
})();
