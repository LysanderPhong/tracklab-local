'use strict';
fetch('/api/config').then(r=>r.json()).then(c=>{if(c.android_download)document.getElementById('android-download').hidden=false;if(c.desktop_download)document.getElementById('desktop-download').hidden=false;}).catch(()=>{});
