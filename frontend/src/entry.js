import { createApp } from "vue";

try {
if (new URLSearchParams(window.location.search).get("mode") === "editor") {
  const { default: NetworkEditorEntry } = await import("./NetworkEditorEntry.vue");
  createApp(NetworkEditorEntry).mount("#app");
} else {
  const [{ default: LifeCircleEntry }] = await Promise.all([import("./LifeCircleEntry.vue"), import("./demo.css")]);
  createApp(LifeCircleEntry).mount("#app");
}
} catch (error) {
  const message = document.createElement("p");
  message.textContent = `区域包未能加载：${error.message}。请确认后端和区域数据已就绪。`;
  message.style.cssText = "max-width:680px;margin:18vh auto;padding:24px;font:16px/1.8 sans-serif;color:#173c3a";
  document.getElementById("app").replaceChildren(message);
}
