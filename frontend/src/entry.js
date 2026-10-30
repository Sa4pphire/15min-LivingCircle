import { createApp } from "vue";

function showMessage(text) {
  const panel = document.createElement("main");
  panel.style.cssText = "max-width:680px;margin:18vh auto;padding:24px;font:16px/1.8 sans-serif;color:#173c3a";
  const message = document.createElement("p");
  message.textContent = text;
  panel.append(message);
  document.getElementById("app").replaceChildren(panel);
}

try {
  const [{ default: LifeCircleEntry }] = await Promise.all([import("./LifeCircleEntry.vue"), import("./demo.css")]);
  createApp(LifeCircleEntry).mount("#app");
} catch (error) {
  showMessage(`服务未能加载：${error.message}。请确认后端和区域数据已就绪。`);
}
