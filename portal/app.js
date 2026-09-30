'use strict';
// A launcher may reuse an already-open tab and only change its fragment.
window.addEventListener('hashchange',()=>{if(new URLSearchParams(location.hash.slice(1)).has('connect'))location.reload();});
// Consume fragment before map requests; credentials never enter the HTTP URL.
const incomingConnection = new URLSearchParams(location.hash.slice(1)).get('connect');
if(incomingConnection!==null)history.replaceState(null,'',location.pathname+location.search);
const $ = id => document.getElementById(id);
const storageKey = 'tracklab.favorites.v1';
let mode = 'cloud', localToken = '', credentials = null, online = false, busy = false, checking = null, refreshing = false;
let operation='route', capabilities=[], routePoints=[], drawing=false, routePreview=null, routeRevision=0, previewTimer=null, routeLayer=null, vertexLayer=null, previewDot=null;
let device = {ready:false, active:false, needs_clear:false}, command = null, selected = null, favorites = [], lastCommand = '';
const clock = value => `${Math.floor(Math.max(0,value)/60).toString().padStart(2,'0')}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,'0')}`;
function note(message, error=false){ $('notice').textContent=message; $('notice').classList.toggle('error',error); }
function validPlace(p){return p&&typeof p.name==='string'&&p.name.length<=160&&typeof p.address==='string'&&p.address.length<=300&&Number.isFinite(p.latitude)&&Math.abs(p.latitude)<=90&&Number.isFinite(p.longitude)&&Math.abs(p.longitude)<=180;}
try {const value=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(value))favorites=value.filter(validPlace).slice(0,20);}catch{note('常用地点未能读取，可重新选择并收藏。',true);}
try {const value=JSON.parse(sessionStorage.getItem('tracklab.connection')||'null');if(value&&typeof value.session==='string'&&typeof value.token==='string')credentials=value;}catch{}
const map=L.map('map',{zoomControl:true,attributionControl:true}).setView([18.405,110.015],14);
// Browser caching and multiplexing keep map panning independent of the Python service.
const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,minZoom:2,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>'}).addTo(map);
tiles.on('tileerror',()=>{$('map-error').hidden=false;});
let marker=null;
const icon=L.divIcon({className:'pin',html:'<span></span>',iconSize:[28,34],iconAnchor:[12,28]});
function choose(place, pan=true, keepRoute=false){
  if(!validPlace(place))return;
  if(device.active){note('请先结束当前定位再更换地点或路线。',true);return;}
  if(!keepRoute)resetRoute();
  selected={...place};
  $('selected-coordinate').textContent=`WGS-84：${place.longitude.toFixed(7)}, ${place.latitude.toFixed(7)}`;
  $('place-name').textContent=place.name;$('place-address').textContent=place.address||'已在地图上选择，请检查标记位置。';
  if(marker)marker.setLatLng([place.latitude,place.longitude]);
  else{marker=L.marker([place.latitude,place.longitude],{icon,draggable:true,keyboard:true,title:'所选地点，可拖动微调'}).addTo(map);marker.on('dragend',()=>{const p=marker.getLatLng();choose({name:'地图上选的位置',address:'已手动微调，请确认位置后收藏。',latitude:p.lat,longitude:p.lng},false);});}
  if(pan)map.setView([place.latitude,place.longitude],Math.max(map.getZoom(),16));
  $('results').hidden=true;$('favorite-form').hidden=true;controls();
}
map.on('click',event=>{if(device.active)return;if(operation==='route'&&drawing){if(routePoints.length>=256){note('最多 256 个路线点。',true);return;}routePoints.push([event.latlng.lat,event.latlng.wrap().lng]);routeChanged();}else choose({name:'地图上选的位置',address:'可拖动标记微调，然后收藏为常用地点。',latitude:event.latlng.lat,longitude:event.latlng.wrap().lng},false);});
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
async function api(path,data,auth=true,timeout=75000){
  const headers={};if(data!==undefined)headers['Content-Type']='application/json';
  if(auth&&mode==='cloud'&&credentials)headers.Authorization=`Bearer ${credentials.session}.${credentials.token}`;
  if(auth&&mode==='local'&&localToken)headers['X-TrackLab-Token']=localToken;
  let response;
  try{response=await fetch(path,{method:data===undefined?'GET':'POST',headers,body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(timeout)});}catch{throw new Error('服务未连接，请检查网络。设备端会在连接失效后停止续期，并尝试恢复定位。');}
  const result=await response.json();if(!response.ok){const error=new Error(result.error||'请求未完成。');error.status=response.status;throw error;}return result;
}
function controls(){
  const connected=mode==='local'?!!localToken:!!credentials;
  const waiting=command&&['queued','delivered'].includes(command.state);
  const blocked=busy||waiting;
  $('legacy-pair').hidden=mode==='local'||connected;
  $('connect-help').hidden=mode==='local'||connected;
  $('scan').hidden=!connected;$('unpair').hidden=mode==='local'||!connected;
  $('refresh-connection').disabled=busy||refreshing;
  $('refresh-connection').textContent=refreshing?'正在刷新…':'刷新连接状态';
  $('pair-button').disabled=busy;$('scan').disabled=blocked||!online||device.active;
  const reason=TrackConnection.startReason({connected,busy:busy||refreshing,online,waiting,ready:device.ready,active:device.active,needsClear:device.needs_clear,selected,operation,preview:routePreview,confirmed:$('confirm-route').checked,drawing,supported:mode==='local'||capabilities.includes('route')});
  $('start').disabled=!!reason;
  $('start-reason').textContent=reason||'设备和地点均已就绪，可以开始。';
  $('start').title=reason;
  $('route-demo').disabled=$('start').disabled;
  $('start').textContent=operation==='route'?'开始动态回放 ↗':'开始固定定位 ↗';
  $('pause-route').hidden=!device.active||device.mode!=='route';$('pause-route').disabled=blocked||!online;
  $('pause-route').textContent=device.state==='paused'?'继续路线':'暂停路线';
  $('route-support').textContent=credentials&&!capabilities.includes('route')?'动态模式需要 0.4 版电脑/Android 连接程序，请更新后重新配对。':'';
  for(const id of ['mode-route','mode-fixed','distance-km','route-pace','route-variation','find-tracks','draw-route','undo-point','finish-route','preset-route','make-oval','preview-route','confirm-route'])$(id).disabled=!!device.active||busy;
  $('clear').disabled=blocked||!online||!(device.ready||device.active||device.needs_clear);
  $('unpair').disabled=blocked;$('duration').disabled=blocked||device.active;
  $('save-place').disabled=!selected;$('signal').classList.toggle('online',online&&connected);
  const platform=mode==='local'?'本机 iPhone 连接':({ 'iphone-mac':'Mac · iPhone','iphone-windows':'Windows · iPhone','android':'Android' }[credentials?.platform]||'连接程序');
  $('connection-title').textContent=connected?(online?(device.ready?'设备已就绪':`${platform} 连接程序在线`):'连接程序暂时离线'):'先连接你的设备';
  $('connection-detail').textContent=connected?(online?(device.message||'点击“检查设备”，确认手机连接状态。'):'检查网络，并保持连接程序运行。当前状态尚未确认。'):'iPhone 推荐本机一键直连：插线后打开“一键连接”，无需配对。点击右侧下载或查看步骤。Android 使用手机应用连接。';
  const names={idle:'尚未开始',starting:'正在连接',running:device.mode==='route'?'动态回放中':'固定定位中',paused:'动态路线已暂停',stopping:'正在恢复',finished:'已到时',stopped:'已停止',failed:'操作未完成'};
  $('state-label').textContent=!online&&connected?'设备状态未知':(names[device.state]||'等待检查');
  $('remaining').textContent=online&&device.active&&device.duration?clock(device.duration-(device.elapsed||0)):'—';
  $('progress').value=device.duration?Math.min(1,(device.elapsed||0)/device.duration):0;
  $('state-detail').textContent=(device.mode==='route'&&device.active?`已回放 ${((device.distance||0)/1000).toFixed(2)} km · `:'')+(device.needs_clear&&!device.active?'恢复尚未确认，请保持原手机连接，点击恢复按钮。':device.cleared?'清除指令已完成，请在手机地图核实真实位置。':device.active?'刷新或关闭网页不会结束本次定位。可点击恢复，或等待到时结束。':'开始后请保留手机与连接程序的连接。');
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
async function pairConnection(path, payload){
  busy=true;controls();
  try{
    const next=await api(path,payload,false);
    credentials=next;command=null;device={ready:false};online=false;capabilities=[];
    try{sessionStorage.setItem('tracklab.connection',JSON.stringify(credentials));}catch{note('浏览器无法保存连接，刷新前请先恢复定位。',true);}
    $('pair-code').value='';
    await pollOnce();
    if(credentials&&online)note('网页已连接。请点击“检查设备”；这一步不会修改手机定位。');
  }catch(error){note(error.message,true);}finally{busy=false;controls();}
  if(credentials&&online)await refreshConnection();
}
$('pair-form').onsubmit=async e=>{e.preventDefault();await pairConnection('/api/pair',{code:$('pair-code').value.trim()});};
$('unpair').onclick=async()=>{busy=true;controls();try{await api('/api/unpair',{});credentials=null;command=null;online=false;device={ready:false};try{sessionStorage.removeItem('tracklab.connection');}catch{}note('已解除配对。连接程序会停止续期，请检查手机是否恢复真实位置。');}catch(error){note(error.message,true);}finally{busy=false;controls();}};
async function send(action,payload={}){
  if(busy)return;busy=true;controls();
  const inProgress={scan:'正在检查手机连接…',clear:'正在恢复真实定位，请保持手机连接…',fixed:'正在检查设备并启动固定定位…',route:'正在检查设备并启动动态路线…'};
  if(inProgress[action])note(inProgress[action]);
  try{
    if(mode==='local'){
      const result=await api('/api/'+(action==='route'?'start':action),payload);
      if(action==='scan'){device={...device,ready:result.ready,message:result.message};note(result.message);}
      if(action==='clear'){
        device.ready=false;note(result.message);
        if(await pollOnce()&&!device.active&&!device.needs_clear){
          try{
            const scan=await api('/api/scan',{});
            device={...device,ready:scan.ready,message:scan.message};
            note(scan.ready?'恢复指令已完成，设备已就绪，可以选择新地点再次开始。请在手机地图核实真实位置。':scan.message,!scan.ready);
          }catch(error){note('恢复指令已完成，但设备检查失败：'+error.message,true);}
        }
      }
      if(action==='route')note('动态路线已启动，请在手机地图检查。');
      if(action==='fixed')note('固定定位已启动，请在手机地图检查位置。');
      await pollOnce();
    }else{
      await api('/api/command',{action,id:crypto.randomUUID(),...payload});
      command={state:'queued'};note('指令已发送，等待连接程序确认。');await pollOnce();
    }
  }catch(error){note(error.message,true);}finally{busy=false;controls();}
}
$('scan').onclick=()=>send('scan');$('clear').onclick=()=>send('clear');
$('start').onclick=()=>{if(operation==='route'){if(routePreview&&$('confirm-route').checked)send('route',routeData(false));}else if(selected)send('fixed',{latitude:selected.latitude,longitude:selected.longitude,seconds:Number($('duration').value)});};
$('route-demo').onclick=()=>{if(routePreview&&$('confirm-route').checked)send('route',routeData(true));};
$('pause-route').onclick=()=>send(device.state==='paused'?'resume':'pause');
function applyStatus(result){
  if(mode==='local'){device={...device,...result.replay,needs_clear:result.needs_clear};online=true;}
  else{online=result.online;device=result.device;capabilities=result.capabilities||[];command=result.command;if(command&&['done','failed','expired','unknown'].includes(command.state)&&lastCommand!==command.id+command.state){lastCommand=command.id+command.state;note(command.message,command.state!=='done');}}
}
function connectionError(error){
  online=false;
  if(error.status===401){credentials=null;command=null;device={ready:false};try{sessionStorage.removeItem('tracklab.connection');}catch{}}
  note(error.message,true);
}
async function pollOnce(){
  if(checking)return checking;
  if(!(mode==='local'?localToken:credentials))return false;
  checking=(async()=>{
    try{applyStatus(await api('/api/status',undefined,true,12000));return true;}
    catch(error){connectionError(error);return false;}
    finally{checking=null;controls();}
  })();
  return checking;
}
async function refreshConnection(){
  if(refreshing||busy)return;
  if(!(mode==='local'?localToken:credentials)){note('此网页尚未连接或连接已失效。请重新打开连接器，并使用它打开的网页；刷新不能代替授权。',true);controls();return;}
  refreshing=true;controls();
  try{
    if(checking)await checking;
    if(!(mode==='local'?localToken:credentials))return;
    if(mode==='local'){
      if(!await pollOnce())return;
      if(!device.active&&!device.needs_clear)await send('scan');
      else note('已刷新当前状态，定位运行或待恢复期间不重复扫描。');
    }else{
      const result=await api('/api/refresh',{id:crypto.randomUUID()},true,15000);
      applyStatus(result);note(result.message,!result.online);
    }
  }catch(error){connectionError(error);}finally{refreshing=false;controls();}
}
$('refresh-connection').onclick=refreshConnection;
async function poll(){if(!refreshing)await pollOnce();setTimeout(poll,1500);}

function stopAnimation(){if(previewTimer)clearInterval(previewTimer);previewTimer=null;if(previewDot){map.removeLayer(previewDot);previewDot=null;}}
function invalidateRoute(){routeRevision++;routePreview=null;$('confirm-route').checked=false;stopAnimation();$('route-summary').textContent='路线或参数已更改，请重新检查。';}
function resetRoute(){routePoints=[];drawing=false;invalidateRoute();drawRoute();}
function drawRoute(){
  if(routeLayer)map.removeLayer(routeLayer);if(vertexLayer)map.removeLayer(vertexLayer);
  routeLayer=null;vertexLayer=L.layerGroup().addTo(map);
  if(routePoints.length){
    routeLayer=L.polyline(drawing?routePoints:[...routePoints,routePoints[0]],{color:'#3b754e',weight:4,dashArray:drawing?'6 6':null}).addTo(map);
    routePoints.forEach((p,i)=>{
      const h=L.marker(p,{draggable:true,icon:L.divIcon({className:'route-vertex',iconSize:[10,10],iconAnchor:[5,5]}),title:`路线点 ${i+1}，可拖动调整`}).addTo(vertexLayer);
      h.on('dragend',()=>{if(device.active){drawRoute();return;}const v=h.getLatLng();routePoints[i]=[v.lat,v.wrap().lng];routeChanged();});
    });
  }
  $('map-hint').textContent=drawing?'沿跑道依次点击添加点，完成后点“闭合路线”':'点击地图选地点 · 路线上的小圆点可拖动';
  $('route-info').textContent=routePoints.length?`${routePoints.length} 个路线点 · ${drawing?'正在画线，尚未闭合':'已闭合，请核对地图后检查路线'}`:'先在全国地图上选择操场，再查询跑道或手动画线。';
  controls();
}
function routeChanged(){invalidateRoute();drawRoute();try{localStorage.setItem('tracklab.route.v1',JSON.stringify({points:routePoints,place:selected}));}catch{}}
function setRoute(points,label){
  if(device.active)return;
  routePoints=points.map(p=>p.map(v=>Math.round(v*1e7)/1e7));drawing=false;
  if(routePoints.length>1&&L.latLng(routePoints[0]).distanceTo(L.latLng(routePoints.at(-1)))<.1)routePoints.pop();
  if(routePoints.length){const center=L.latLngBounds(routePoints).getCenter();choose({name:label,address:'路线来源不等于实地测绘，请逐段检查跑道。',latitude:center.lat,longitude:center.lng},false,true);}
  routeChanged();if(routeLayer)map.fitBounds(routeLayer.getBounds(),{padding:[35,35],maxZoom:19});
}
function routeData(demo=false){return {points:routePoints.map(p=>p.map(v=>Math.round(v*1e7)/1e7)),distance_km:Number($('distance-km').value),pace:Number($('route-pace').value),variation:$('route-variation').checked,demo};}
function setOperation(value){
  if(device.active)return;operation=value;
  $('mode-route').setAttribute('aria-pressed',value==='route');$('mode-fixed').setAttribute('aria-pressed',value==='fixed');
  $('route-settings').hidden=value!=='route';$('route-tools').hidden=value!=='route';$('route-demo').hidden=value!=='route';$('fixed-settings').hidden=value!=='fixed';
  drawing=false;stopAnimation();if(routeLayer)routeLayer.setStyle({opacity:value==='route'?1:0});if(vertexLayer)value==='route'?map.addLayer(vertexLayer):map.removeLayer(vertexLayer);
  $('map-hint').textContent=value==='route'?'选择操场后查找跑道，或手动画线':'点击地图选点 · 拖动标记微调';
  try{localStorage.setItem('tracklab.mode',value);}catch{}controls();
}
$('mode-route').onclick=()=>setOperation('route');$('mode-fixed').onclick=()=>setOperation('fixed');
$('coordinate-form').onsubmit=e=>{e.preventDefault();try{const p=TrackGeometry.coordinates(`${$('longitude').value},${$('latitude').value}`);if(!p)throw new Error('请输入有效经纬度。');choose(p);note('已定位到输入的 WGS-84 坐标。');}catch(error){note(error.message,true);}};
$('draw-route').onclick=()=>{if(device.active)return;routePoints=[];drawing=true;routeChanged();note('请沿跑道依次点击至少 4 个点。转弯处多加几个点，最后闭合。');};
$('undo-point').onclick=()=>{if(device.active)return;routePoints.pop();drawing=true;routeChanged();};
$('finish-route').onclick=()=>{if(routePoints.length<4){note('至少需要 4 个路线点。',true);return;}setRoute(routePoints,selected?.name||'手绘操场路线');};
$('make-oval').onclick=()=>{if(!selected){note('请先选择操场中心位置。',true);return;}try{setRoute(TrackGeometry.ellipse(selected.latitude,selected.longitude,Number($('oval-length').value),Number($('oval-width').value),Number($('oval-angle').value)),'待调整的椭圆草图');note('已生成草图，必须对照实际跑道调整长度、宽度、角度或拖动路线点。');}catch(error){note(error.message,true);}};
$('find-tracks').onclick=async()=>{
  if(!selected){note('请先选择操场附近的位置。',true);return;}
  const startPlace=selected;busy=true;controls();$('route-info').textContent='正在查询所选位置周围 1.5 km 内的公开跑道…';
  try{const data=await api(`/api/tracks?lat=${startPlace.latitude}&lon=${startPlace.longitude}`,undefined,false);if(selected!==startPlace)return;
    $('track-results').replaceChildren();
    for(const t of data.tracks){const b=document.createElement('button');b.className='result';b.textContent=`${t.name} · 约 ${t.lap_metres} 米/圈`;b.onclick=()=>{setRoute(t.points,t.name);$('track-results').hidden=true;};$('track-results').append(b);}
    $('track-results').hidden=!data.tracks.length;$('route-info').textContent=data.message;
  }catch(error){$('route-info').textContent=error.message+' 可继续手动画线。';}finally{busy=false;controls();}
};
$('preset-route').onclick=async()=>{busy=true;controls();try{const data=await api('/api/route-preset',undefined,false);setRoute(data.points,data.name);note('已载入原陵水体育场近似路线，请核对；不代表学校认可的围栏。');}catch(error){note(error.message,true);}finally{busy=false;controls();}};
for(const id of ['distance-km','route-pace','route-variation'])$(id).oninput=()=>{invalidateRoute();controls();};
$('confirm-route').onchange=controls;
$('preview-route').onclick=async()=>{
  if(drawing){note('请先闭合路线。',true);return;}
  const revision=routeRevision;busy=true;controls();
  try{const data=await api(mode==='local'?'/api/preview':'/api/route-preview',routeData());if(revision!==routeRevision)return;
    routePreview=data;$('route-summary').textContent=`单圈约 ${Math.round(data.lap_metres)} 米 · 共 ${data.distance_km} km · 预计 ${clock(data.duration)}。动画仅展示一圈，不操作手机。`;
    const pts=data.track.map(p=>L.latLng(p.latitude,p.longitude)),lens=[0];for(let i=1;i<pts.length;i++)lens.push(lens.at(-1)+pts[i-1].distanceTo(pts[i]));
    stopAnimation();previewDot=L.circleMarker(pts[0],{radius:7,color:'#ffffff',fillColor:'#9a672f',fillOpacity:1}).addTo(map);const began=performance.now();
    previewTimer=setInterval(()=>{const distance=((performance.now()-began)/12000)*lens.at(-1);if(distance>=lens.at(-1)){stopAnimation();return;}let i=1;while(lens[i]<distance)i++;const f=(distance-lens[i-1])/(lens[i]-lens[i-1]);previewDot.setLatLng([pts[i-1].lat+f*(pts[i].lat-pts[i-1].lat),pts[i-1].lng+f*(pts[i].lng-pts[i-1].lng)]);},50);
    note('路线检查通过。请核对地图上的闭合路线，再勾选确认；开始按钮才会操作设备。');
  }catch(error){routePreview=null;note(error.message,true);}finally{busy=false;controls();}
};

(async()=>{
  renderFavorites();
  try{const saved=JSON.parse(localStorage.getItem('tracklab.route.v1')||'null');if(saved&&Array.isArray(saved.points)&&saved.points.length>=4&&saved.points.length<=256&&saved.points.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=85&&Math.abs(p[1])<=180)){if(validPlace(saved.place))selected=saved.place;setRoute(saved.points,saved.place?.name||'上次编辑的路线');}setOperation(localStorage.getItem('tracklab.mode')==='fixed'?'fixed':'route');}catch{setOperation('route');}
  controls();
  try{const config=await api('/api/config',undefined,false);mode=config.mode;if(config.preview)$('version').textContent='开发预览 · 尚未上线';if(mode==='local'){credentials=null;const boot=await api('/api/bootstrap',undefined,false);localToken=boot.token;device={...device,...boot.replay,needs_clear:boot.needs_clear};online=true;$('legacy-link').hidden=true;}$('connection-detail').textContent=mode==='local'?'用数据线连接 iPhone，解锁后点击检查设备。':$('connection-detail').textContent;controls();if(mode==='local'&&!device.active&&!device.needs_clear)await refreshConnection();if(mode==='cloud'&&incomingConnection!==null){const match=/^([A-Za-z0-9_-]{32})\.([A-Za-z0-9_-]{43})$/.exec(incomingConnection);if(match)await pairConnection('/api/pair-link',{session:match[1],token:match[2]});else note('连接链接格式不正确，请从连接程序重新打开。',true);}poll();}
  catch(error){note(error.message,true);}
})();
