let projectRequest = null;
let projectSwitching = false;

async function loadProjects() {
 if (projectRequest || projectSwitching) return;
 const status = $('#projects-status');
 projectRequest = fetch('/api/projects');
 try {
  const response = await projectRequest;
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || 'Could not load projects.');
  if (projectSwitching) return;
  const rows = result.projects.map(project => {
   const counts = project.counts;
   const jobs = [counts.running ? counts.running+' rendering' : '', counts.pending ? counts.pending+' queued' : ''].filter(Boolean);
   const detail = !project.available ? 'Project folder unavailable' : jobs.join(' · ') || (project.running ? 'Controller running' : '');
   return `<article class="settings-card">
    <div class="settings-object-heading"><div><h3>${esc(project.name)}</h3>
    <p class="settings-address">${esc(project.path)}</p></div>
    ${project.current ? '<span class="pill">Current project</span>' : `<button class="btn" data-open-project="${esc(project.id)}" ${project.available?'':'disabled'}>Open</button>`}</div>
    ${detail ? `<p class="muted">${esc(detail)}</p>` : ''}
   </article>`;
  });
  const list = $('#project-list');
  const focused = document.activeElement?.dataset.openProject;
  if (!same('projects', result.projects)) {
   list.innerHTML = rows.join('');
   if (focused) list.querySelector(`[data-open-project="${CSS.escape(focused)}"]`)?.focus();
  }
  status.textContent = '';
 } catch (error) {
  status.textContent = error.message;
 } finally {
  projectRequest = null;
 }
}

async function openWorkspace(body, button, status) {
 if (projectSwitching) return;
 projectSwitching = true;
 button.disabled = true;
 status.textContent = 'Opening project…';
 try {
  const response = await fetch('/api/projects', {
   method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || 'Could not open project.');
  if (result.current) {
   projectSwitching = false;
   closeModal();
   status.textContent = '';
   return;
  }
  if (!Number.isInteger(result.port) || result.port < 1 || result.port > 65535) {
   throw new Error('The project controller did not return a valid port.');
  }
  window.location.replace('http://127.0.0.1:'+result.port+'/');
 } catch (error) {
  status.textContent = error.message;
 } finally {
  projectSwitching = false;
  button.disabled = false;
 }
}

function projectDialog(action) {
 const create = action === 'create';
 const picker = window.webkit?.messageHandlers?.chooseProjectFolder;
 const browse = !create && picker ? '<button type="button" id="project-browse" class="btn">Browse…</button>' : '';
 modal(`<div class="dlg-h"><h2>${create?'New project':'Open existing project'}</h2></div>
  <form id="project-form"><div class="dlg-b">
   <label class="f" for="project-input">${create?'Project name':'Full project folder path'}</label>
   <div class="toolbar"><input id="project-input" required ${create?'maxlength="120"':''}>${browse}</div>
   ${create?'':'<p class="muted">Choose the folder that contains studio.db.</p>'}
   <p id="project-dialog-status" role="status"></p>
  </div><div class="dlg-f"><button type="button" class="btn" onclick="closeModal()">Cancel</button>
  <button id="project-submit" class="btn pri">${create?'Create project':'Open project'}</button></div></form>`,
  {beforeClose:()=>!projectSwitching});
 if (browse) $('#project-browse').onclick = () => picker.postMessage({});
 $('#project-form').onsubmit = event => {
  event.preventDefault();
  const value = $('#project-input').value.trim();
  openWorkspace({action, [create?'name':'path']:value}, $('#project-submit'), $('#project-dialog-status'));
 };
}

$('#project-list').addEventListener('click', event => {
 const button = event.target.closest('[data-open-project]');
 if (button) openWorkspace({action:'switch', id:button.dataset.openProject}, button, $('#projects-status'));
});
setInterval(() => { if (tab === 'projects') loadProjects(); }, 5000);
