'use strict';
const $ = id => document.getElementById(id);
const storageKey = 'tracklab.favorites.v1';
let localToken='', online=false, busy=false, checking=null, refreshing=false, operationPending=false, busyAction="", statusEpoch=0;
let tracksLoading=false, previewLoading=false, presetLoading=false, editing=false, vertexMarkers=[];
let operation='route', routePoints=[], drawing=false, routePreview=null, routeRevision=0, previewTimer=null, routeLayer=null, vertexLayer=null, previewDot=null;
let device = {ready:false, active:false, needs_clear:false}, selected = null, favorites = [];
const clock = value => `${Math.floor(Math.max(0,value)/60).toString().padStart(2,'0')}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,'0')}`;
function note(message, error=false){ $('notice').textContent=message; $('notice').classList.toggle('error',error); }
function validPlace(p){return p&&typeof p.name==='string'&&p.name.length<=160&&typeof p.address==='string'&&p.address.length<=300&&Number.isFinite(p.latitude)&&Math.abs(p.latitude)<=90&&Number.isFinite(p.longitude)&&Math.abs(p.longitude)<=180;}
try {const value=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(value))favorites=value.filter(validPlace).slice(0,20);}catch{note('常用地点未能读取，可重新选择并收藏。',true);}
const map=L.map('map',{zoomControl:true,attributionControl:true,preferCanvas:true}).setView([18.405,110.015],14);
// Browser caching and multiplexing keep map panning independent of the Python service.
const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,minZoom:2,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>'}).addTo(map);
tiles.on('tileerror',()=>{$('map-error').hidden=false;});
let marker=null;
const icon=L.divIcon({className:'pin',html:'<span></span>',iconSize:[28,34],iconAnchor:[12,28]});
function targetLocked(){return !!device.active||(busy&&busyAction!=='scan')||(operationPending&&busyAction!=='scan');}
function choose(place, pan=true, keepRoute=false){
  if(!validPlace(place))return;
  if(targetLocked()){note('请先完成当前设备操作再更换地点或路线。',true);return;}
  if(!keepRoute)resetRoute();
  selected={...place};
  $('selected-coordinate').textContent=`WGS-84：${place.longitude.toFixed(7)}, ${place.latitude.toFixed(7)}`;
  $('place-name').textContent=place.name;$('place-address').textContent=place.address||'已在地图上选择，请检查标记位置。';
  if(marker)marker.setLatLng([place.latitude,place.longitude]);
  else{marker=L.marker([place.latitude,place.longitude],{icon,draggable:true,keyboard:true,title:'所选地点，可拖动微调'}).addTo(map);marker.on('dragend',()=>{if(targetLocked()){marker.setLatLng([selected.latitude,selected.longitude]);return;}const p=marker.getLatLng();choose({name:'地图上选的位置',address:'已手动微调，请确认位置后收藏。',latitude:p.lat,longitude:p.lng},false);});}
  if(pan)map.setView([place.latitude,place.longitude],Math.max(map.getZoom(),16));
  $('results').hidden=true;$('favorite-form').hidden=true;controls();
}
map.on('click',event=>{if(targetLocked())return;if(operation==='route'&&drawing){if(routePoints.length>=256){note('最多 256 个路线点。',true);return;}routePoints.push([event.latlng.lat,event.latlng.wrap().lng]);routeChanged();}else choose({name:'地图上选的位置',address:'可拖动标记微调，然后收藏为常用地点。',latitude:event.latlng.lat,longitude:event.latlng.wrap().lng},false);});
$('recenter').onclick=()=>{if(selected)map.setView([selected.latitude,selected.longitude],16);};
function renderFavorites(){
  $('favorites-list').replaceChildren();
  if(!favorites.length){const p=document.createElement('p');p.className='muted';p.textContent='收藏地点后，下次直接选择。';$('favorites-list').append(p);}
  favorites.forEach((p,index)=>{
    const box=document.createElement('div');box.className='favorite';
    const button=document.createElement('button');button.textContent=p.name;button.onclick=()=>choose(p);
    const remove=document.createElement('button');remove.className='delete';remove.textContent='×';remove.setAttribute('aria-label',`删除收藏 ${p.name}`);
    remove.onclick=()=>{const next=favorites.filter((_,i)=>i!==index);if(saveFavorites(next)){favorites=next;renderFavorites();}};
    box.append(button,remove);$('favorites-list').append(box);
  });
}
function saveFavorites(next){try{localStorage.setItem(storageKey,JSON.stringify(next));return true;}catch{note('浏览器未允许保存收藏，请检查隐私设置。',true);return false;}}
$('save-place').onclick=()=>{$('favorite-form').hidden=false;$('favorite-name').value=selected.name==='地图上选的位置'?'':selected.name;$('favorite-name').focus();};
$('favorite-form').onsubmit=e=>{
  e.preventDefault();if(!selected)return;
  const name=$('favorite-name').value.trim();if(!name){note('请给地点起一个名字。',true);return;}
  if(favorites.length>=20){note('最多保存 20 个常用地点，请先删除不需要的收藏。',true);return;}
  const next=[...favorites,{...selected,name}];if(saveFavorites(next)){favorites=next;renderFavorites();$('favorite-form').hidden=true;note('地点已收藏，仅保存在这个浏览器。');}
};
async function api(path,data,auth=true,timeout=15000){
  const headers={};if(data!==undefined)headers['Content-Type']='application/json';
  if(auth&&localToken)headers['X-TrackLab-Token']=localToken;
  let response;
  try{response=await fetch(path,{method:data===undefined?'GET':'POST',headers,body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(timeout)});}
  catch(error){throw new Error(error.name==='TimeoutError'?'等待超时，请刷新状态确认结果；设备操作可能仍在继续。':'本机服务未连接，请重新打开启动文件。正在运行的定位不会因网页断开而立即结束。');}
  const result=await response.json();if(!response.ok){const error=new Error(result.error||'请求未完成。');error.status=response.status;throw error;}return result;
}
function text(id,value){if($(id).textContent!==value)$(id).textContent=value;}
function controls(){
  const connected=!!localToken,blocked=busy||operationPending;
  $('refresh-connection').disabled=blocked||refreshing;
  text('refresh-connection',refreshing?'正在检查…':'刷新连接状态');
  const reason=TrackConnection.startReason({connected,busy:blocked||refreshing,online,ready:device.ready,active:device.active,needsClear:device.needs_clear,selected,operation,preview:routePreview,confirmed:$('confirm-route').checked,drawing,supported:true});
  $('start').disabled=!!reason;text('start-reason',reason||'设备和地点均已就绪，可以开始。');$('start').title=reason;
  $('route-demo').disabled=$('start').disabled;
  text('start',operation==='route'?'开始动态回放 ↗':'开始固定定位 ↗');
  $('pause-route').hidden=!device.active||device.mode!=='route';$('pause-route').disabled=blocked||!online;
  text('pause-route',device.state==='paused'?'继续路线':'暂停路线');
  for(const id of ['mode-route','mode-fixed','distance-km','route-pace','route-variation','draw-route','edit-route','undo-point','finish-route','make-oval','confirm-route'])$(id).disabled=targetLocked();
  $('find-tracks').disabled=!!device.active||blocked||tracksLoading;
  text('find-tracks',tracksLoading?'正在查询…':'查找附近跑道');
  $('preset-route').disabled=!!device.active||blocked||presetLoading;
  $('preview-route').disabled=!!device.active||blocked||previewLoading;
  text('preview-route',previewLoading?'正在检查…':'检查路线并预览动画');
  $('edit-route').setAttribute('aria-pressed',String(editing));text('edit-route',editing?'完成调整':'调整路线点');
  $('clear').disabled=blocked||!online||!(device.ready||device.active||device.needs_clear);
  $('duration').disabled=blocked||device.active;
  $('exit-service').disabled=blocked||!online||device.active||device.needs_clear;
  if(marker?.dragging)targetLocked()?marker.dragging.disable():marker.dragging.enable();
  $('save-place').disabled=!selected;$('signal').classList.toggle('online',online&&connected);
  text('connection-title',connected?(online?(device.ready?'手机已就绪':'本机服务已连接'):'本机服务暂时离线'):'正在连接本机服务');
  text('connection-detail',connected?(online?(device.message||'用数据线连接并解锁 iPhone，点击刷新检查。'):'重新打开启动文件后刷新页面。'):'打开下载包内的一键连接文件，无需配对码。');
  const names={idle:'尚未开始',starting:'正在连接',running:device.mode==='route'?'动态回放中':'固定定位中',paused:'动态路线已暂停',stopping:'正在恢复',finished:'已到时',stopped:'已停止',failed:'操作未完成'};
  text('state-label',!online&&connected?'设备状态未知':(names[device.state]||'等待检查'));
  text('remaining',online&&device.active&&device.duration?clock(device.duration-(device.elapsed||0)):'—');
  $('progress').value=device.duration?Math.min(1,(device.elapsed||0)/device.duration):0;
  text('state-detail',(device.mode==='route'&&device.active?`已回放 ${((device.distance||0)/1000).toFixed(2)} km · `:'')+(device.needs_clear&&!device.active?'恢复尚未确认，请保持原手机连接，点击恢复按钮。':device.cleared?'清除指令已完成，请在手机地图核实真实位置。':device.active?'刷新或关闭网页不会结束本次定位。可点击恢复，或等待到时结束。':'开始后请保留手机与电脑的连接。'));
}
$('search-form').onsubmit=async e=>{
  e.preventDefault();const q=$('query').value.trim();if(q.length<2)return;
  try{const p=TrackGeometry.coordinates(q);if(p){choose(p);$('search-info').textContent='已按 WGS-84 经纬度定位，不依赖地名收录。';return;}}catch(error){$('search-info').textContent=error.message;return;}
  $('search-button').disabled=true;$('search-info').textContent='正在查找地点…';$('results').hidden=true;
  try{const result=await api('/api/search?q='+encodeURIComponent(q)+'&provider='+$('search-provider').value,undefined,false);$('results').replaceChildren();
    for(const p of result.results){if(!validPlace(p))continue;const b=document.createElement('button');b.className='result';const title=document.createElement('span');title.textContent=p.name;const desc=document.createElement('small');desc.textContent=p.address;b.append(title,desc);b.onclick=()=>{choose(p);$('search-info').textContent='地点已选中，可拖动标记微调。';};$('results').append(b);}
    $('results').hidden=!$('results').children.length;$('search-info').textContent=$('results').children.length?'选择一个结果，确认地图上的位置。':'未找到地点。试试“城市＋街道”或在地图上选点；开放地图可能没有收录。';
  }catch(error){$('search-info').textContent=error.message;}finally{$('search-button').disabled=false;}
};
async function send(action,payload={}){
  if(busy||operationPending)return;if(action!=='scan'){stopAnimation();editing=false;drawRoute();}busy=true;busyAction=action;controls();
  const inProgress={scan:'正在检查手机连接…',clear:'正在恢复真实定位，请保持手机连接…',fixed:'正在检查设备并启动固定定位…',route:'正在检查设备并启动动态路线…'};
  if(inProgress[action])note(inProgress[action]);
  try{
    const result=await api('/api/'+(action==='route'?'start':action),payload,true,75000);
    statusEpoch++;applyStatus({replay:result.replay||{},needs_clear:result.needs_clear??device.needs_clear,operation_pending:false});
    if(action==='scan'){device={...device,ready:result.ready,message:result.message};note(result.message);}
    if(action==='clear'){
      device.ready=false;note(result.message);
      if(await pollFresh()&&!device.active&&!device.needs_clear){
        try{
          const scan=await api('/api/scan',{},true,20000);
          device={...device,ready:scan.ready,message:scan.message};
          note(scan.ready?'恢复指令已完成，设备已就绪，可以选择新地点再次开始。请在手机地图核实真实位置。':scan.message,!scan.ready);
        }catch(error){note('恢复指令已完成，但设备检查失败：'+error.message,true);}
      }
    }
    if(action==='route'||action==='fixed')note('定位已启动，请在手机地图检查位置。');
    if(action!=='clear')await pollFresh();
  }catch(error){statusEpoch++;operationPending=true;await pollFresh();note(error.message,true);}finally{busy=false;busyAction="";controls();}
}
$('clear').onclick=()=>send('clear');
$('exit-service').onclick=async()=>{
  if(busy||operationPending||device.active||device.needs_clear)return;
  busy=true;controls();
  try{await api('/api/shutdown',{});localToken='';online=false;note('本机服务已退出。下次双击一键连接文件即可重新打开。');}
  catch(error){note(error.message,true);}finally{busy=false;controls();}
};
$('start').onclick=()=>{if(operation==='route'){if(routePreview&&$('confirm-route').checked)send('route',routeData(false));}else if(selected)send('fixed',{latitude:selected.latitude,longitude:selected.longitude,seconds:Number($('duration').value)});};
$('route-demo').onclick=()=>{if(routePreview&&$('confirm-route').checked)send('route',routeData(true));};
$('pause-route').onclick=()=>send(device.state==='paused'?'resume':'pause');
function applyStatus(result){
  device={...device,...result.replay,needs_clear:result.needs_clear};
  operationPending=!!result.operation_pending;online=true;
}
function connectionError(error){online=false;note(error.message,true);}
async function pollOnce(){
  if(checking)return checking;
  if(!localToken)return false;
  const epoch=statusEpoch;checking=(async()=>{
    try{const result=await api('/api/status',undefined,true,5000);if(epoch!==statusEpoch)return false;applyStatus(result);return true;}
    catch(error){if(epoch===statusEpoch)connectionError(error);return false;}
    finally{checking=null;controls();}
  })();
  return checking;
}
async function pollFresh(){if(checking)await checking;return pollOnce();}
async function refreshConnection(){
  if(refreshing||busy||operationPending)return;
  if(!localToken){note('本机服务尚未连接，请重新打开启动文件。',true);return;}
  refreshing=true;controls();
  try{
    if(!await pollOnce())return;
    if(!device.active&&!device.needs_clear&&!operationPending)await send('scan');
    else note('已刷新当前状态，定位运行或待恢复期间不重复扫描。');
  }finally{refreshing=false;controls();}
}
$('refresh-connection').onclick=refreshConnection;
function pollDelay(){return document.hidden?30000:!online?10000:targetLocked()?1500:5000;}
async function poll(){if(!refreshing)await pollOnce();setTimeout(poll,pollDelay());}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollOnce();else stopAnimation();});

function stopAnimation(){if(previewTimer)clearInterval(previewTimer);previewTimer=null;if(previewDot){map.removeLayer(previewDot);previewDot=null;}}
function invalidateRoute(){routeRevision++;routePreview=null;$('confirm-route').checked=false;stopAnimation();$('route-summary').textContent='路线或参数已更改，请重新检查。';}
function resetRoute(){routePoints=[];drawing=false;editing=false;invalidateRoute();drawRoute();}
function drawRoute(){
  const points=drawing?routePoints:routePoints.length?[...routePoints,routePoints[0]]:[];
  if(!routeLayer)routeLayer=L.polyline([],{color:'#3b754e',weight:4,interactive:false});
  routeLayer.setLatLngs(points).setStyle({dashArray:drawing?'6 6':null});
  if(operation==='route'&&points.length)map.addLayer(routeLayer);else map.removeLayer(routeLayer);
  if(!vertexLayer)vertexLayer=L.layerGroup();
  const showHandles=operation==='route'&&(drawing||editing)&&!device.active;
  if(showHandles){
    while(vertexMarkers.length>routePoints.length)vertexLayer.removeLayer(vertexMarkers.pop());
    routePoints.forEach((p,i)=>{
      let h=vertexMarkers[i];
      if(!h){h=L.marker(p,{draggable:true,icon:L.divIcon({className:'route-vertex',iconSize:[10,10],iconAnchor:[5,5]}),title:`路线点 ${i+1}，可拖动调整`}).addTo(vertexLayer);vertexMarkers.push(h);
        h.on('dragend',()=>{if(targetLocked()){drawRoute();return;}const v=h.getLatLng();routePoints[i]=[v.lat,v.wrap().lng];routeChanged();});}
      else h.setLatLng(p);
    });map.addLayer(vertexLayer);
  }else map.removeLayer(vertexLayer);
  text('map-hint',drawing?'沿跑道依次点击添加点，完成后点“闭合路线”':editing?'拖动小圆点调整路线，完成后再次检查路线':operation==='route'?'点击地图选地点 · 点“调整路线点”微调路线':'点击地图选点 · 拖动标记微调');
  text('route-info',routePoints.length?`${routePoints.length} 个路线点 · ${drawing?'正在画线，尚未闭合':'已闭合，请核对地图后检查路线'}`:'先在全国地图上选择操场，再查询跑道或手动画线。');
  controls();
}
function routeChanged(){invalidateRoute();drawRoute();try{localStorage.setItem('tracklab.route.v1',JSON.stringify({points:routePoints,place:selected}));}catch{}}
function setRoute(points,label){
  if(targetLocked())return;
  routePoints=points.map(p=>p.map(v=>Math.round(v*1e7)/1e7));drawing=false;editing=false;
  if(routePoints.length>1&&L.latLng(routePoints[0]).distanceTo(L.latLng(routePoints.at(-1)))<.1)routePoints.pop();
  if(routePoints.length){const center=L.latLngBounds(routePoints).getCenter();choose({name:label,address:'路线来源不等于实地测绘，请逐段检查跑道。',latitude:center.lat,longitude:center.lng},false,true);}
  routeChanged();if(routeLayer)map.fitBounds(routeLayer.getBounds(),{padding:[35,35],maxZoom:19});
}
function routeData(demo=false){return {points:routePoints.map(p=>p.map(v=>Math.round(v*1e7)/1e7)),distance_km:Number($('distance-km').value),pace:Number($('route-pace').value),variation:$('route-variation').checked,demo};}
function setOperation(value){
  if(targetLocked())return;if(operation!==value)invalidateRoute();operation=value;
  $('mode-route').setAttribute('aria-pressed',value==='route');$('mode-fixed').setAttribute('aria-pressed',value==='fixed');
  $('route-settings').hidden=value!=='route';$('route-tools').hidden=value!=='route';$('route-demo').hidden=value!=='route';$('fixed-settings').hidden=value!=='fixed';
  drawing=false;editing=false;stopAnimation();drawRoute();
  $('map-hint').textContent=value==='route'?'选择操场后查找跑道，或手动画线':'点击地图选点 · 拖动标记微调';
  try{localStorage.setItem('tracklab.mode',value);}catch{}controls();
}
$('mode-route').onclick=()=>setOperation('route');$('mode-fixed').onclick=()=>setOperation('fixed');
$('coordinate-form').onsubmit=e=>{e.preventDefault();try{const p=TrackGeometry.coordinates(`${$('longitude').value},${$('latitude').value}`);if(!p)throw new Error('请输入有效经纬度。');choose(p);note('已定位到输入的 WGS-84 坐标。');}catch(error){note(error.message,true);}};
$('draw-route').onclick=()=>{if(targetLocked())return;routePoints=[];drawing=true;routeChanged();note('请沿跑道依次点击至少 4 个点。转弯处多加几个点，最后闭合。');};
$('edit-route').onclick=()=>{if(targetLocked())return;editing=!editing;drawRoute();};
$('undo-point').onclick=()=>{if(targetLocked())return;routePoints.pop();drawing=true;routeChanged();};
$('finish-route').onclick=()=>{if(routePoints.length<4){note('至少需要 4 个路线点。',true);return;}setRoute(routePoints,selected?.name||'手绘操场路线');};
$('make-oval').onclick=()=>{if(!selected){note('请先选择操场中心位置。',true);return;}try{setRoute(TrackGeometry.ellipse(selected.latitude,selected.longitude,Number($('oval-length').value),Number($('oval-width').value),Number($('oval-angle').value)),'待调整的椭圆草图');note('已生成草图，必须对照实际跑道调整长度、宽度、角度或拖动路线点。');}catch(error){note(error.message,true);}};
$('find-tracks').onclick=async()=>{
  if(!selected){note('请先选择操场附近的位置。',true);return;}
  if(tracksLoading)return;const startPlace=selected,revision=routeRevision;tracksLoading=true;controls();$('route-info').textContent='正在查询所选位置周围 1.5 km 内的公开跑道…';
  try{const data=await api(`/api/tracks?lat=${startPlace.latitude}&lon=${startPlace.longitude}`,undefined,false);if(selected!==startPlace||operation!=='route'||revision!==routeRevision||targetLocked())return;
    $('track-results').replaceChildren();
    for(const t of data.tracks){const b=document.createElement('button');b.className='result';b.textContent=`${t.name} · 约 ${t.lap_metres} 米/圈`;b.onclick=()=>{if(selected!==startPlace||revision!==routeRevision||operation!=='route')return;setRoute(t.points,t.name);$('track-results').hidden=true;};$('track-results').append(b);}
    $('track-results').hidden=!data.tracks.length;$('route-info').textContent=data.message;
  }catch(error){if(selected===startPlace&&revision===routeRevision&&operation==='route'&&!targetLocked())$('route-info').textContent=error.message+' 可继续手动画线。';}finally{tracksLoading=false;controls();}
};
$('preset-route').onclick=async()=>{
  if(presetLoading)return;const revision=routeRevision;presetLoading=true;controls();
  try{const data=await api('/api/route-preset',undefined,false);if(revision!==routeRevision||operation!=='route'||targetLocked())return;setRoute(data.points,data.name);note('已载入原陵水体育场近似路线，请核对；不代表学校认可的围栏。');}
  catch(error){if(revision===routeRevision&&operation==='route'&&!targetLocked())note(error.message,true);}
  finally{presetLoading=false;controls();}
};
for(const id of ['distance-km','route-pace','route-variation'])$(id).oninput=()=>{invalidateRoute();controls();};
$('confirm-route').onchange=controls;
$('preview-route').onclick=async()=>{
  if(drawing){note('请先闭合路线。',true);return;}
  if(previewLoading)return;const revision=routeRevision;previewLoading=true;controls();
  try{const data=await api('/api/preview',routeData());if(revision!==routeRevision||operation!=='route'||targetLocked())return;
    routePreview=data;$('route-summary').textContent=`单圈约 ${Math.round(data.lap_metres)} 米 · 共 ${data.distance_km} km · 预计 ${clock(data.duration)}。动画仅展示一圈，不操作手机。`;
    const pts=data.track.map(p=>L.latLng(p.latitude,p.longitude)),lens=[0];for(let i=1;i<pts.length;i++)lens.push(lens.at(-1)+pts[i-1].distanceTo(pts[i]));
    stopAnimation();previewDot=L.circleMarker(pts[0],{radius:7,color:'#ffffff',fillColor:'#9a672f',fillOpacity:1}).addTo(map);const began=performance.now();
    previewTimer=setInterval(()=>{const distance=((performance.now()-began)/12000)*lens.at(-1);if(distance>=lens.at(-1)){stopAnimation();return;}let i=1;while(lens[i]<distance)i++;const f=(distance-lens[i-1])/(lens[i]-lens[i-1]);previewDot.setLatLng([pts[i-1].lat+f*(pts[i].lat-pts[i-1].lat),pts[i-1].lng+f*(pts[i].lng-pts[i-1].lng)]);},50);
    note('路线检查通过。请核对地图上的闭合路线，再勾选确认；开始按钮才会操作设备。');
  }catch(error){if(revision===routeRevision&&operation==='route'&&!targetLocked()){routePreview=null;note(error.message,true);}}finally{previewLoading=false;controls();}
};

(async()=>{
  renderFavorites();
  try{const saved=JSON.parse(localStorage.getItem('tracklab.route.v1')||'null');if(saved&&Array.isArray(saved.points)&&saved.points.length>=4&&saved.points.length<=256&&saved.points.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=85&&Math.abs(p[1])<=180)){if(validPlace(saved.place))selected=saved.place;setRoute(saved.points,saved.place?.name||'上次编辑的路线');}setOperation(localStorage.getItem('tracklab.mode')==='fixed'?'fixed':'route');}catch{setOperation('route');}
  controls();
  try{
    const boot=await api('/api/bootstrap',undefined,false,5000);localToken=boot.token;applyStatus(boot);
    text('version',`本地版 · ${boot.version}`);controls();
    // Map editing stays available during the USB check; polling starts immediately.
    if(!device.active&&!device.needs_clear&&!operationPending)send('scan');
    setTimeout(poll,pollDelay());
  }catch(error){note(error.message,true);}
})();
