// Follows the processing log over a WebSocket, reconnecting when the connection is lost,
// and reloads the page when a run whose status it shows (job processing, a database update,
// or a conversion) starts or finishes, so its status is current. A conversion's progress is
// shown as it is reported.
(function () {
  "use strict";

  var log = document.getElementById("log");
  var connection = document.getElementById("connection");
  if (!log || !("WebSocket" in window)) {
    return;
  }

  var MAX_LINES = 2000;
  var url = new URL(log.dataset.socket, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  // The kinds of run whose status the page shows, and whether each was in progress when the
  // page loaded, from data-busy-jobs, data-busy-database, and data-busy-convert attributes.
  var kinds = ["jobs", "database", "convert"].filter(function (kind) {
    return log.dataset[busyKey(kind)] !== undefined;
  });
  var pageBusy = kinds.map(function (kind) {
    return log.dataset[busyKey(kind)];
  }).join(" ");
  var progress = document.getElementById("convert-progress");
  var lines = log.textContent.split("\n").filter(function (line) { return line !== ""; });
  var delay = 1000;

  function busyKey(kind) {
    return "busy" + kind.charAt(0).toUpperCase() + kind.slice(1);
  }

  function isNearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  }

  function show(newLines, reset) {
    var stick = isNearBottom();
    lines = reset ? newLines.slice() : lines.concat(newLines);
    if (lines.length > MAX_LINES) {
      lines = lines.slice(lines.length - MAX_LINES);
    }
    log.textContent = lines.join("\n") + (lines.length ? "\n" : "");
    if (stick) {
      log.scrollTop = log.scrollHeight;
    }
  }

  function connect() {
    var socket = new WebSocket(url.toString());
    socket.onopen = function () {
      delay = 1000;
      connection.textContent = "Live";
    };
    socket.onmessage = function (event) {
      var message = JSON.parse(event.data);
      if (message.type === "lines") {
        show(message.lines, message.reset);
      } else if (message.type === "status") {
        var busy = kinds.map(function (kind) {
          return String(message[kind].state !== "idle");
        }).join(" ");
        if (busy !== pageBusy) {
          window.location.reload();
        } else if (progress && message.convert && message.convert.progress) {
          progress.textContent = message.convert.progress;
        }
      }
    };
    socket.onclose = function () {
      connection.textContent = "Reconnecting…";
      window.setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000);
    };
  }

  log.scrollTop = log.scrollHeight;
  connect();
}());
