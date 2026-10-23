/* Runs Python from a chat reply in the browser (Pyodide), modelled on Open WebUI's
   pyodideSandboxHost.ts. Two walls: the code runs in a Worker (so a `while True` cannot freeze the page
   and is killed on timeout) inside an iframe with an opaque origin (so it cannot use the user's login
   cookie against /api), and a CSP stops it reaching any other address (no uploads, no downloads).
   window.runPython(code, seconds) -> Promise<{stdout, stderr, images}>. Files: /pyodide/ (scripts/fetch_pyodide.py). */
(() => {
  const ORIGIN = location.origin;
  const WORKER = `
    importScripts(${JSON.stringify(ORIGIN + '/pyodide/pyodide.js')});
    let py = loadPyodide({ indexURL: ${JSON.stringify(ORIGIN + '/pyodide/')} });
    onmessage = async ({ data: { id, code } }) => {
      let out = '', err = '', images = [];
      try {
        py = await py;
        py.runPython("import os; os.environ['MPLBACKEND'] = 'AGG'");
        py.setStdout({ batched: (s) => { out += s + '\\n'; } });
        py.setStderr({ batched: (s) => { err += s + '\\n'; } });
        await py.loadPackagesFromImports(code, { messageCallback: () => {}, errorCallback: () => {} });
        await py.runPythonAsync(code);
        if (py.loadedPackages.matplotlib) images = py.runPython(
          'import matplotlib.pyplot as p, io, base64\\n' +
          'r = []\\n' +
          'for n in p.get_fignums():\\n b = io.BytesIO(); p.figure(n).savefig(b, format="png"); r.append("data:image/png;base64," + base64.b64encode(b.getvalue()).decode())\\n' +
          'p.close("all")\\nr').toJs();
      } catch (e) { err += (e && e.message) || String(e); }
      postMessage({ id, stdout: out, stderr: err, images });
    };`;
  // 'unsafe-eval' is not needed by Pyodide; connect-src is this server only, so the code cannot send data out.
  const PAGE = `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'wasm-unsafe-eval' blob: ${ORIGIN}; connect-src ${ORIGIN}; worker-src blob:"><script>
    const w = new Worker(URL.createObjectURL(new Blob([${JSON.stringify(WORKER)}])));
    w.onmessage = (e) => parent.postMessage(e.data, '*');
    w.onerror = (e) => parent.postMessage({ id: -1, stderr: 'Python could not start: ' + e.message }, '*');
    addEventListener('message', (e) => { if (e.source === parent) w.postMessage(e.data); });
  <\/script>`;

  let frame = null, ready = null, next = 1;
  const kill = () => { frame && frame.remove(); frame = null; };

  window.runPython = (code, seconds = 60) => new Promise((resolve) => {
    if (!frame) {
      frame = document.createElement('iframe');
      frame.setAttribute('sandbox', 'allow-scripts'); // no allow-same-origin: that is the wall
      frame.hidden = true;
      frame.srcdoc = PAGE;
      ready = new Promise((r) => frame.addEventListener('load', r, { once: true }));
      document.body.append(frame);
    }
    const id = next++, win = frame.contentWindow;
    const done = (r) => { clearTimeout(timer); removeEventListener('message', on); resolve(r); };
    const on = (e) => {
      if (e.source !== win || (e.data.id !== id && e.data.id !== -1)) return;
      if (e.data.id === -1) kill();
      done({ stdout: '', stderr: '', images: [], ...e.data });
    };
    const timer = setTimeout(() => { kill(); done({ stdout: '', stderr: `Stopped: ran longer than ${seconds} s.`, images: [] }); }, seconds * 1000);
    addEventListener('message', on);
    // The first run also loads Python (a few seconds); the worker holds the message until it has.
    ready.then(() => win.postMessage({ id, code }, '*'));
  });
})();
