// Native port keeps this worker alive. Only the named ERP input page is used.
let port;
let busy = false;
let timer;
function connect() {
  if (port) return;
  port = chrome.runtime.connectNative('com.nenovakakao.sales_session');
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    port = null; busy = false; clearInterval(timer);
    chrome.action.setBadgeText({text:'OFF'});
  });
  port.onMessage.addListener(async message => {
    if (message.type !== 'job') { busy = false; return; }
    const job = message.job;
    const tabs = await chrome.tabs.query({url:'https://nenovaweb.com/sales/defect-deductions*'});
    let result = {status:0,data:{success:false}};
    // Do not broadcast a write to several tabs, or retry a failed send.
    if (tabs.length) {
      try { result = await chrome.tabs.sendMessage(tabs[0].id, {type:'nenova-defect-job',job}); }
      catch { /* A dispatched write stays unknown, never retried here. */ }
    }
    port?.postMessage({type:'result',id:job.id,...result});
  });
  timer = setInterval(async () => {
    if (busy || !port) return;
    busy = true;
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
