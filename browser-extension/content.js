// Same-origin request uses the existing HttpOnly session. No cookie access.
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || location.origin !== 'https://nenovaweb.com'
      || location.pathname !== '/sales/defect-deductions') return;
  if (message.type === 'nenova-defect-ping') { reply({ready:true}); return; }
  if (message.type !== 'nenova-defect-job') return;
  const job = message.job;
  if (job.expires_at * 1000 <= Date.now() || !['GET','POST'].includes(job.method)) return;
  if (job.method === 'POST' && !['save','rematch'].includes(job.body?.action)) return;
  (async () => {
    try {
      const url = new URL('/api/sales/defect-deductions',location.origin);
      if (job.method === 'GET') {
        url.search = new URLSearchParams(job.params).toString();
      }
      const response = await fetch(url, {method:job.method,credentials:'same-origin',redirect:'error',
        headers:{'Content-Type':'application/json'},
        body:job.method==='POST'?JSON.stringify(job.body):undefined,
        signal:AbortSignal.timeout(12000)});
      const data = await response.json();
      reply({status:response.status,data});
    } catch { reply({status:0,data:{success:false}}); }
  })();
  return true;
});
