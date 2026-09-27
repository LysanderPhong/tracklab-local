# 第三方组件与网络服务

- pymobiledevice3 11.19.2：运行时通过 PyPI 安装，GPL-3.0；上游 https://github.com/doronz88/pymobiledevice3 ，许可证附于 licenses/pymobiledevice3-LICENSE。其依赖锁定于 uv.lock，未将本机虚拟环境附入发布包。
- Leaflet 1.9.4：BSD-2-Clause；保留 portal/vendor 中的发行文件和 licenses/leaflet-LICENSE，来源 https://leafletjs.com/ 。
- OpenStreetMap：页面保留地图署名。公共图块使用政策：https://operations.osmfoundation.org/policies/tiles/ 。
- Photon：https://github.com/komoot/photon ，公共演示接口没有可用性保证。
- Nominatim：https://operations.osmfoundation.org/policies/nominatim/ 。
- Overpass：https://wiki.openstreetmap.org/wiki/Overpass_API 。

本地版不附带 Esri 卫星图片、地图缓存、Android APK 或设备凭据。网络服务内容不属于本项目 GPL 授权范围，使用时仍遵守各自条款。
