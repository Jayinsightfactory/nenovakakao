// Same-origin request uses the existing HttpOnly session. No cookie access.
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || location.origin !== 'https://nenovaweb.com'
      || location.pathname !== '/sales/defect-deductions') return;
  if (message.type === 'nenova-defect-ping') { reply({ready:true}); return; }
  if (['nenova-defect-screenshot-prepare','nenova-defect-screenshot-check'].includes(message.type)) {
    (async () => {
      try {
        const job = message.job;
        const norm = value => String(value ?? '').replace(/\s+/g,'').toLowerCase();
        if (!(job.expires_at * 1000 > Date.now()) || !job.params?.length) throw Error('expired_screenshot');
        const button = name => [...document.querySelectorAll('button')].find(e => e.innerText.trim() === name);
        const heading = () => [...document.querySelectorAll('strong')].some(e =>
          job.params.every(item => e.innerText.includes(`영업 입력 완료 목록 — ${item.orderYear}년 ${item.orderWeek}차`)));
        if (message.type === 'nenova-defect-screenshot-prepare') {
          // Do not discard unsaved editor rows or change a user's year/week.
          const summary = document.body.innerText;
          if (!/미저장 0건/.test(summary) || !/저장 안 됨 0건/.test(summary)) throw Error('unsaved_rows_or_unknown_view');
          if (!heading()) {
            const done = button('완료 목록');
            if (!done) throw Error('completed_view_missing');
            done.click();
            await new Promise(resolve => setTimeout(resolve, 150));
          }
          if (!heading()) throw Error('screenshot_year_week_mismatch');
          const manager = [...document.querySelectorAll('select')].find(e =>
            [...e.options].some(o => o.textContent.trim() === '정재훈'));
          const owner = job.params[0].managerName;
          if (!manager || job.params.some(item => item.managerName !== owner)) throw Error('receipt_owner_mismatch');
          // Refresh after API saves; an already-open completion list is stale.
          const ownerButton = button(owner);
          if (!ownerButton || ownerButton.disabled) throw Error('receipt_owner_view_unavailable');
          ownerButton.click();
          for (let i=0; i<100 && button('조회')?.disabled; i++)
            await new Promise(resolve => setTimeout(resolve,100));
          if (button('조회')?.disabled) throw Error('receipt_list_refresh_timeout');
        }
        const find = () => job.params.map(item => {
          if (!item.note?.startsWith(`kakao-defect:${job.request_id}:`)) throw Error('receipt_marker_missing');
          const matches = [...document.querySelectorAll('tbody tr')].filter(row => {
            const cells = [...row.querySelectorAll('td')].map(e => norm(e.innerText));
            return cells.length === 12 && cells[1] === norm(item.managerName)
              && cells[2] === norm(item.customerName) && cells[4].endsWith(norm(item.matchedProductName))
              && cells[5] === norm(`${item.quantity}${item.sourceUnit}`)
              && cells[8] === norm(item.note) && cells[9] === norm('저장 완료');
          });
          if (matches.length !== 1) throw Error('saved_row_not_unique');
          return matches[0];
        });
        let rows;
        for (let tries=0; tries<20; tries++) {
          try { rows=find(); break; } catch (error) {
            if (message.type.endsWith('-check') || tries===19) throw error;
            await new Promise(resolve => setTimeout(resolve,100));
          }
        }
        if (!heading()) throw Error('screenshot_year_week_mismatch');
        if (message.type.endsWith('-prepare')) {
          let style = document.getElementById('nenova-receipt-highlight-style');
          if (!style) {
            style = document.createElement('style');
            style.id = 'nenova-receipt-highlight-style';
            style.textContent = 'tr[data-nenova-receipt-highlight] > td {background:#fff3a3 !important;color:#111 !important;border-top:3px solid #e8a000 !important;border-bottom:3px solid #e8a000 !important;}';
            document.head.appendChild(style);
          }
          document.querySelectorAll('[data-nenova-receipt-highlight]').forEach(e => e.removeAttribute('data-nenova-receipt-highlight'));
          rows.forEach(row => row.setAttribute('data-nenova-receipt-highlight', job.id));
          // Temporary display decoration only; do not alter saved ERP values.
          setTimeout(() => rows.forEach(row => {
            if (row.getAttribute('data-nenova-receipt-highlight') === job.id)
              row.removeAttribute('data-nenova-receipt-highlight');
          }), 30000);
          rows[0].scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }
        const rects = rows.map(row => row.getBoundingClientRect());
        if (rects.some(r => r.height<=0 || r.top<0 || r.bottom>innerHeight || r.left<0 || r.right>innerWidth))
          throw Error('saved_row_not_fully_visible');
        // Keep the full viewport; these bounds verify the target is not clipped.
        if (rows.length !== 1) throw Error('multiple_receipt_rows_need_separate_capture');
        const r=rects[0];
        reply({ready:true,clip:{x:r.x,y:r.y,width:r.width,height:r.height},viewport:{width:innerWidth,height:innerHeight},
          signature:JSON.stringify(job.params)+rows.map(row=>row.innerText).join('\n')});
      } catch(error) { reply({ready:false,error:String(error.message || error)}); }
    })();
    return true;
  }
  if (message.type !== 'nenova-defect-job') return;
  const job = message.job;
  if (job.expires_at * 1000 <= Date.now() || !['GET','POST','MASTER'].includes(job.method)) return;
  if (job.method === 'MASTER' && (job.request_id !== 'defect-master'
      || !['products','customers'].includes(job.params?.entity))) return;
  const order = /^ORD-[A-F0-9]{8}$/.test(job.request_id) || job.request_id === 'order-history';
  if (job.request_id === 'order-history' && job.method !== 'GET') return;
  if (order && !['GET','POST'].includes(job.method)) return;
  if (order && job.method === 'POST' && (job.body?.source !== 'sales-paste' || job.body?.orderMode !== 'ADD')) return;
  if (!order && job.method === 'POST' && !['save','rematch'].includes(job.body?.action)) return;
  (async () => {
    try {
      const master = job.method === 'MASTER';
      const url = new URL(order ? (job.method === 'GET' ? '/api/orders/my-customers' : '/api/orders') : master ? '/api/master' : '/api/sales/defect-deductions',location.origin);
      if (job.method === 'GET' || master) {
        url.search = new URLSearchParams(job.params).toString();
      }
      const response = await fetch(url, {method:master ? 'GET' : job.method,credentials:'same-origin',redirect:'error',
        headers:{'Content-Type':'application/json'},
        body:job.method==='POST'?JSON.stringify(job.body):undefined,
        signal:AbortSignal.timeout(order && job.method === 'POST' ? 45000 : 12000)});
      const data = await response.json();
      if (master) {
        const items = data.data || data[job.params.entity];
        reply({status:response.status,data:{success:response.ok && Array.isArray(items),
          items, entity:job.params.entity,error:data.error}});
      } else reply({status:response.status,data});
    } catch { reply({status:0,data:{success:false}}); }
  })();
  return true;
});
