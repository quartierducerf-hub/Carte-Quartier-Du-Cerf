const C="cerf-v1";
self.addEventListener("install",()=>self.skipWaiting());
self.addEventListener("activate",e=>e.waitUntil(clients.claim()));
self.addEventListener("fetch",e=>{
  const r=e.request,u=new URL(r.url);
  if(r.method!=="GET")return;
  const tuiles=/arcgisonline|opentopomap|openstreetmap/.test(u.hostname);
  const local=u.origin===location.origin,cdn=u.hostname==="cdnjs.cloudflare.com";
  if(!(tuiles||local||cdn))return;
  e.respondWith((async()=>{
    const c=await caches.open(C),hit=await c.match(r);
    if(hit&&(tuiles||cdn))return hit;                       // tuiles et bibliothèques : cache d’abord
    try{
      let n;
      try{n=await fetch(tuiles?new Request(r.url,{mode:"cors"}):r)}catch(_){n=await fetch(r)}
      if(n&&n.ok)c.put(r,n.clone());
      return n;
    }catch(_){if(hit)return hit;throw _}                    // hors réseau : version gardée
  })());
});
