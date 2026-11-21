// Follows the processing log over a WebSocket, reconnecting when the connection is lost,
// and reloads the page when a job processing run or database update starts or finishes,
// so its status is current.
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
  // Whether job processing and a database update were in progress when the page loaded.
  var pageBusy = log.dataset.busyJobs + " " + log.dataset.busyDatabase;
  var lines = log.textContent.split("\n").filter(function (line) { return line !== ""; });
  var delay = 1000;

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
        var busy = String(message.jobs.state !== "idle") + " "
          + String(message.database.state !== "idle");
        if (busy !== pageBusy) {
          window.location.reload();
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
