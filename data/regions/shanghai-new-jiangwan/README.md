# 新江湾城路网演示区域包

版本：0.1.0。当前为合成演示数据，保留全部现有人工校对和编辑记录，未实地核实。

范围由 `network.json` 的路段路径自动派生，不需要独立 `boundary.geojson`。当前有 6,576 个节点、7,345 条边，数据外接范围约 5.23 × 5.21 公里。边缘分析可能受数据截断影响。

将本目录放入系统的 `data/regions/`，配置 `REGION_ID=shanghai-new-jiangwan` 后重启服务。程序和 C++ 引擎另行安装；这个包保存区域数据，不包含程序依赖。

数据来源：OpenStreetMap contributors，ODbL 1.0：<https://www.openstreetmap.org/copyright>。校准网格保留其原有来源和转换记录。设施候选缓存和 API 密钥未包含在包内；已标注设施列表当前为空。

人工修改通过本地区的路网编辑器保存；它更新网络、源标注及清单校验和。归档前运行 `python backend/scripts/package_region.py --archive`。完整字段、校验与维护说明见项目的 `docs/region-packages.md`。
