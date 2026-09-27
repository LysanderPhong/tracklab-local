'use strict';
const TrackGeometry = {
  coordinates(value) {
    const match = value.trim().match(/^([-+]?\d+(?:\.\d+)?)\s*[,，]\s*([-+]?\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const longitude = Number(match[1]), latitude = Number(match[2]);
    if (Math.abs(longitude)>180 || Math.abs(latitude)>85) throw new Error('坐标超出范围。输入顺序为经度,纬度，纬度范围为 -85 到 85。');
    return {name:'经纬度所选位置',address:'WGS-84 · 请核对地图位置',latitude,longitude};
  },
  ellipse(lat, lon, length, width, angle) {
    if (![lat,lon,length,width,angle].every(Number.isFinite) || length<20 || length>500 || width<20 || width>500 || Math.abs(lat)>85 || Math.abs(angle)>360) throw new Error('长短轴应为 20–500 米，角度为 -360° 至 360°。');
    const a=angle*Math.PI/180, points=[];
    for(let i=0;i<64;i++){
      const t=i*2*Math.PI/64, north=length/2*Math.cos(t), east=width/2*Math.sin(t);
      points.push([lat+(north*Math.cos(a)-east*Math.sin(a))/111195,lon+(north*Math.sin(a)+east*Math.cos(a))/(111195*Math.cos(lat*Math.PI/180))]);
    }
    return points;
  }
};
if(typeof module!=='undefined')module.exports=TrackGeometry;
