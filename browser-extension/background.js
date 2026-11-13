// Native port keeps this worker alive. Only the named ERP input page is used.
let port;
let busy = false;
let timer;
let openingTab;
let reconnectTimer;
let reconnectDelay = 1000;
let busySince = 0;
async function ensureErpTab() {
  const tabs = await chrome.tabs.query({url:'https://nenovaweb.com/sales/defect-deductions*'});
  if (tabs.length) return tabs.find(tab => tab.active) || tabs[0];
  if (!openingTab) openingTab = chrome.tabs.create({url:'https://nenovaweb.com/sales/defect-deductions?popup=1',active:true})
    .finally(() => { openingTab = null; });
  return openingTab;
}
function connect() {
  if (port) return;
  clearTimeout(reconnectTimer);
  port = chrome.runtime.connectNative('com.nenovakakao.sales_session');
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    port = null; busy = false; clearInterval(timer);
    chrome.action.setBadgeText({text:'OFF'});
    // Reconnect the transport only. Dispatched jobs remain in their journal;
    // never resend a write whose result was lost.
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 60000);
  });
  port.onMessage.addListener(async message => {
    reconnectDelay = 1000;
    if (message.type === 'open_page') {
      try { await ensureErpTab(); } finally { busy = false; }
      return;
    }
    if (message.type !== 'job') { busy = false; return; }
    const job = message.job;
    const tabs = await chrome.tabs.query({url:'https://nenovaweb.com/sales/defect-deductions*'});
    let result = {status:0,data:{success:false}};
    // Do not broadcast a write to several tabs, or retry a failed send.
    if (tabs.length) {
      try {
        if (job.method === 'SCREENSHOT') {
          const tab = await ensureErpTab();
          await chrome.tabs.update(tab.id, {active:true});
          await chrome.windows.update(tab.windowId, {focused:true});
          const checked = await chrome.tabs.sendMessage(tab.id, {type:'nenova-defect-screenshot-prepare',job});
          if (!checked?.ready) throw new Error(checked?.error || 'saved_row_not_visible');
          let png = await chrome.tabs.captureVisibleTab(tab.windowId, {format:'png'});
          const active = await chrome.tabs.query({active:true,windowId:tab.windowId});
          if (active[0]?.id !== tab.id) throw new Error('tab_changed');
          const verified = await chrome.tabs.sendMessage(tab.id, {type:'nenova-defect-screenshot-check',job});
          if (!verified?.ready || verified.signature !== checked.signature || JSON.stringify(verified.clip) !== JSON.stringify(checked.clip))
            throw new Error(verified?.error || 'saved_row_changed');
          // Preserve the whole viewport and column headings. A thin row crop
          // is center-cropped by Kakao previews and hides customer/product text.
          if (png.length > 700000) throw new Error('screenshot_too_large');
          result = {status:200,data:{success:true,png}};
        } else result = await chrome.tabs.sendMessage(tabs[0].id, {type:'nenova-defect-job',job});
      }
      catch(error) { result = {status:0,data:{success:false,error:String(error.message || error)}}; }
    }
    port?.postMessage({type:'result',id:job.id,...result});
  });
  timer = setInterval(async () => {
    if (busy && Date.now() - busySince > 60000) {
      // A lost ping/result must not freeze every later queue request.
      port?.disconnect();
      return;
    }
    if (busy || !port) return;
    busy = true;
    busySince = Date.now();
    try {
      const tabs = await chrome.tabs.query({url:'https://nenovaweb.com/sales/defect-deductions*'});
      let ready = false;
      if (tabs.length) {
        try { ready = (await chrome.tabs.sendMessage(tabs[0].id,{type:'nenova-defect-ping'}))?.ready === true; }
        catch { /* Page needs reload after extension installation. */ }
      }
      chrome.action.setBadgeText({text:ready?'ON':'TAB'});
      port.postMessage({type:'poll',page_ready:ready});
    } catch { busy = false; }
  }, 1000);
}
chrome.action.onClicked.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
