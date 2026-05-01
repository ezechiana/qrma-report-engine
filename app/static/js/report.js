// app/static/js/report.js

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".collapsible").forEach((el) => {
    el.addEventListener("click", () => {
      el.classList.toggle("open");
    });
  });
});
