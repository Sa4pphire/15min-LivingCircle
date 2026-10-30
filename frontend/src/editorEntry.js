import { createApp } from "vue";
import { loadRuntimeCapabilities } from "./runtimeCapabilities.js";

function showMessage(text) {
  const panel = document.createElement("main");
  panel.style.cssText = "max-width:680px;margin:18vh auto;padding:24px;font:16px/1.8 sans-serif;color:#173c3a";
  const message = document.createElement("p");
  message.textContent = text;
  panel.append(message);
  document.getElementById("app").replaceChildren(panel);
}

try {
  const capabilities = await loadRuntimeCapabilities();
  if (!capabilities.networkEditor) {
    showMessage("当前服务已关闭路网编辑器。请使用文件模式的本地编辑服务。保存的区域包可通过部署流程导入。");
  } else {
    const { default: NetworkEditorEntry } = await import("./NetworkEditorEntry.vue");
    createApp(NetworkEditorEntry).mount("#app");
  }
} catch (error) {
  showMessage(`编辑器未能加载：${error.message}。请确认本地后端和区域数据已就绪。`);
}
