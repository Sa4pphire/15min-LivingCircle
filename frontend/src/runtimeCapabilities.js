// Loaded once before mounting either application. The server remains the
// authority for access; hiding an entry point is only a presentation choice.
let capabilities = Object.freeze({ networkEditor: false });

export async function loadRuntimeCapabilities(fetchImpl = fetch) {
  const response = await fetchImpl('/api/v1/capabilities', { cache: 'no-store' });
  if (!response.ok) throw new Error(`服务配置无法读取 HTTP ${response.status}`);
  const body = await response.json();
  if (typeof body?.networkEditor !== 'boolean') throw new Error('服务配置格式无效');
  capabilities = Object.freeze({ networkEditor: body.networkEditor });
  return capabilities;
}

export function networkEditorEnabled() {
  return capabilities.networkEditor;
}
