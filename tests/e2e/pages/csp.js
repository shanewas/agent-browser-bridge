window.__clicked = false;
document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("btn");
  if (btn) {
    btn.addEventListener("click", () => {
      window.__clicked = true;
    });
  }
});
