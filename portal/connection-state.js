(function(root){
  function startReason(s){
    if(!s.connected)return '尚未连接：请打开下载包内的一键连接文件。';
    if(s.busy)return '正在处理操作，请稍候。';
    if(!s.online)return '本机服务离线：请重新打开启动文件，再点击“刷新连接状态”。';
    if(s.waiting)return '正在等待设备操作结果，请稍候或刷新连接状态。';
    if(s.active)return '定位正在运行，请先点击“恢复真实定位”结束当前操作。';
    if(s.needsClear)return '上次定位尚未恢复，请先点击“恢复真实定位”。';
    if(!s.ready)return '网页已连接，但手机尚未就绪。请点击“一键连接”，按上方提示完成手机确认。';
    if(!s.selected)return '请选择一个地点：搜索后点击结果，或直接点击地图。';
    if(s.operation==='route'){
      if(!s.supported)return '当前连接器不支持动态路线，请更新连接器或切换固定定位。';
      if(s.drawing||!s.preview)return '请完成路线编辑，并点击“检查路线并预览动画”。';
      if(!s.confirmed)return '请核对路线并勾选确认。';
    }
    return '';
  }
  root.TrackConnection={startReason};
  if(typeof module!=='undefined')module.exports=root.TrackConnection;
})(typeof window==='undefined'?globalThis:window);
