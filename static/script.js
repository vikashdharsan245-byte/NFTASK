
let state = { me:null, teams:[], selected:[null,null,null], locked:false, questionnaires:{}, answers:{}, completed:{} };
let openSlot = null;

async function api(path, options={}) {
    const response = await fetch(`/api${path}`, {
        credentials:"include",
        ...options,
        headers:{"Content-Type":"application/json", ...(options.headers || {})}
    });
    let data={};
    try { data=await response.json(); } catch (_) {}
    if(!response.ok) {
        const e=new Error(data.message || "Request failed");
        e.status=response.status;
        throw e;
    }
    return data;
}

function escapeHTML(value) {
    return String(value ?? "")
      .replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");
}

function getTeam(id){ return state.teams.find(t=>t.id===id); }

function showLogin(msg=""){
    document.getElementById("authOverlay").classList.remove("hidden");
    document.getElementById("authMessage").textContent=msg;
}
function hideAuth(){ document.getElementById("authOverlay").classList.add("hidden"); }


function initGoogleSignIn() {
    const clientId = document.body.dataset.googleClientId || "";
    const container = document.getElementById("googleSignIn");

    if (!clientId) {
        document.getElementById("googleLoadError").textContent =
            "Google login is not configured. Add GOOGLE_CLIENT_ID in Vercel.";
        return;
    }

    if (!window.google || !window.google.accounts || !window.google.accounts.id) {
        document.getElementById("googleLoadError").textContent =
            "Google Sign-In could not be loaded. Check browser extensions/network access and try again.";
        return;
    }

    try {
        google.accounts.id.initialize({
            client_id: clientId,
            callback: handleGoogleCredential,
            auto_select: false,
            cancel_on_tap_outside: true,
            context: "signin",
            hosted_domain: "nitt.edu"
        });

        google.accounts.id.renderButton(container, {
            type: "standard",
            theme: "filled_blue",
            size: "large",
            text: "signin_with",
            shape: "rectangular",
            logo_alignment: "left",
            width: 320
        });
    } catch (error) {
        console.error("Google Sign-In initialization failed:", error);
        document.getElementById("googleLoadError").textContent =
            "Could not initialize Google Sign-In.";
    }
}

function waitForGoogleSignIn(attempt = 0) {
    if (window.google?.accounts?.id) {
        initGoogleSignIn();
        return;
    }

    if (attempt >= 30) {
        document.getElementById("googleLoadError").textContent =
            "Google Sign-In is taking too long to load. Refresh the page.";
        return;
    }

    setTimeout(() => waitForGoogleSignIn(attempt + 1), 300);
}

async function loadQuestionnaires(){
    const ids=[...new Set(state.selected.filter(Boolean))];
    const loaded=await Promise.all(ids.map(async id=>{
        const r=await api(`/questionnaires/${encodeURIComponent(id)}`);
        return [id,r.questions];
    }));
    state.questionnaires=Object.fromEntries(loaded);
}

async function loadApplication(){
    const [me,teams,application]=await Promise.all([
        api("/auth/me"),api("/teams"),api("/application")
    ]);
    state.me=me.user;
    state.teams=teams.teams;
    state.selected=[
        application.preferences[0] || null,
        application.preferences[1] || null,
        application.preferences[2] || null
    ];
    state.locked=Boolean(application.locked);
    state.answers=application.answers || {};
    state.completed=application.completed || {};
    if(state.locked) await loadQuestionnaires();
    hideAuth();
    render();
}

async function boot(){
    try { await loadApplication(); }
    catch(e){
        if(e.status===401) { showLogin("Sign in with your NITT Google account to continue."); render(); }
        else { console.error(e); showLogin(e.message); }
    }
}

async function handleGoogleCredential(response){
    if(!response?.credential){
        document.getElementById("authMessage").textContent="Google did not return a credential.";
        return;
    }
    try{
        document.getElementById("authMessage").textContent="Verifying your NITT account...";
        const result=await api("/auth/google",{
            method:"POST",
            body:JSON.stringify({credential:response.credential})
        });
        state.me=result.user;
        await loadApplication();
    }catch(e){
        document.getElementById("authMessage").textContent=e.message;
    }
}
window.handleGoogleCredential=handleGoogleCredential;

async function logout(){
    try{ await api("/auth/logout",{method:"POST"}); }catch(_){}
    state={me:null,teams:[],selected:[null,null,null],locked:false,questionnaires:{},answers:{},completed:{}};
    showLogin("Logged out.");
    render();
}

function render(){
    const picker=document.getElementById("pickerSection");
    const locked=document.getElementById("lockedSection");
    if(!state.me){
        picker.style.display="none"; locked.style.display="none";
        document.getElementById("statusText").textContent="Login required";
        document.getElementById("statusDot").classList.remove("complete");
        return;
    }
    renderStatus();
    if(state.locked){
        picker.style.display="none"; locked.style.display="block";
        renderLockedPreferences(); renderQuestionnaires();
    }else{
        picker.style.display="block"; locked.style.display="none"; renderPicker();
    }
}

function renderStatus(){
    const count=state.selected.filter(Boolean).length;
    if(!state.locked){
        document.getElementById("statusText").textContent=`${count} of 3 preferences selected`;
        document.getElementById("statusDot").classList.remove("complete");
        return;
    }
    const all=state.selected.length===3 && state.selected.every(id=>Boolean(state.completed[id]));
    document.getElementById("statusText").textContent=all ? "Application completed":"Preferences locked - complete your questionnaires";
    document.getElementById("statusDot").classList.toggle("complete",all);
}

function renderPicker(){
    const picker=document.getElementById("picker");
    const count=state.selected.filter(Boolean).length;
    picker.innerHTML=`
      <div class="preference-slots">
        ${[0,1,2].map(slot=>{
          const team=getTeam(state.selected[slot]);
          return `<div class="preference-slot">
            <div class="preference-box" data-slot="${slot}">
              <div class="preference-number">Preference ${slot+1}</div>
              <div class="preference-team ${team?"":"preference-placeholder"}">
                ${team?escapeHTML(team.name):"Click to choose a team"}
              </div>
            </div>
            ${openSlot===slot?`<div class="team-choices">
              ${state.teams.map(t=>{
                const disabled=state.selected.includes(t.id)&&state.selected[slot]!==t.id;
                return `<div class="team-choice ${disabled?"disabled":""}"
                    data-team="${escapeHTML(t.id)}" data-slot="${slot}">
                    <strong>${escapeHTML(t.name)}</strong><br><small>${escapeHTML(t.desc)}</small>
                </div>`;
              }).join("")}
            </div>`:""}
          </div>`;
        }).join("")}
      </div>
      <div class="preference-actions">
        <span class="selection-count">${count} / 3 selected</span>
        <button id="lockChoices" class="primary-btn" ${count!==3?"disabled":""}>Lock In Choices</button>
      </div>`;

    document.querySelectorAll(".preference-box").forEach(box=>{
        box.addEventListener("click",()=>{
            const slot=Number(box.dataset.slot);
            openSlot=openSlot===slot?null:slot; render();
        });
    });

    document.querySelectorAll(".team-choice").forEach(choice=>{
        choice.addEventListener("click",()=>{
            if(choice.classList.contains("disabled")) return;
            state.selected[Number(choice.dataset.slot)]=choice.dataset.team;
            openSlot=null; render();
        });
    });

    document.getElementById("lockChoices")?.addEventListener("click",lockPreferences);
}

async function lockPreferences(){
    if(state.selected.filter(Boolean).length!==3) return;
    if(new Set(state.selected).size!==3){ alert("Please select three different teams."); return; }
    if(!confirm("Are you sure you want to lock your preferences? You cannot change them afterwards.")) return;

    try{
        const result=await api("/application/preferences",{
            method:"PUT",
            body:JSON.stringify({preferences:state.selected})
        });
        state.selected=result.preferences;
        state.locked=result.locked;
        await loadQuestionnaires();
        render();
    }catch(e){ alert(e.message); }
}

function renderLockedPreferences(){
    document.getElementById("lockedPreferences").innerHTML=`
      <div class="locked-preferences">
        ${state.selected.map((id,i)=>{
            const team=getTeam(id);
            return `<div class="locked-preference">
              <div class="locked-label">Preference ${i+1}</div>
              <div class="locked-team">${escapeHTML(team?.name || id)}</div>
            </div>`;
        }).join("")}
      </div>`;
}

function renderQuestionnaires(){
    const container=document.getElementById("questionnaires");
    container.innerHTML="";

    state.selected.forEach((teamId,index)=>{
        const team=getTeam(teamId);
        const questions=state.questionnaires[teamId] || [];
        const done=Boolean(state.completed[teamId]);
        const card=document.createElement("div");
        card.className="questionnaire-card";

        card.innerHTML=`
          <div class="questionnaire-header">
            <div class="questionnaire-title">Preference ${index+1}: ${escapeHTML(team?.name || teamId)}</div>
            <div class="questionnaire-status ${done?"done":""}">${done?"Completed":"Not completed"}</div>
          </div>
          <div class="questionnaire-body">
            ${questions.length?questions.map(q=>{
                const value=state.answers[teamId]?.[q.id] || "";
                if(q.type==="select"){
                    return `<div class="question">
                      <label>${escapeHTML(q.title)}</label>
                      <select class="answer-select answer-field" data-team="${escapeHTML(teamId)}" data-question="${escapeHTML(q.id)}">
                        <option value="">Select an answer</option>
                        ${(q.options||[]).map(o=>`<option value="${escapeHTML(o)}" ${value===o?"selected":""}>${escapeHTML(o)}</option>`).join("")}
                      </select>
                    </div>`;
                }
                return `<div class="question">
                  <label>${escapeHTML(q.title)}</label>
                  <textarea class="answer-field" data-team="${escapeHTML(teamId)}" data-question="${escapeHTML(q.id)}" placeholder="Write your answer here...">${escapeHTML(value)}</textarea>
                </div>`;
            }).join(""):`<p style="color:#c5d8ea;font-size:13px;">No questionnaire has been published for this domain yet.</p>`}
            ${questions.length?`<button class="primary-btn submit-questionnaire" data-team="${escapeHTML(teamId)}">${done?"Update Answers":"Submit Questionnaire"}</button>`:""}
          </div>`;
        container.appendChild(card);
    });

    document.querySelectorAll(".submit-questionnaire").forEach(btn=>{
        btn.addEventListener("click",()=>submitQuestionnaire(btn.dataset.team));
    });

    if(state.selected.length===3 && state.selected.every(id=>Boolean(state.completed[id]))){
        const banner=document.createElement("div");
        banner.className="completion-banner";
        banner.innerHTML="<h3>Application Completed</h3><p>Your preferences and questionnaires have been submitted successfully.</p>";
        container.appendChild(banner);
    }
}

async function submitQuestionnaire(teamId){
    const fields=[...document.querySelectorAll(`.answer-field[data-team="${CSS.escape(teamId)}"]`)];
    const answers={};
    for(const field of fields){
        const value=String(field.value||"").trim();
        if(!value){ alert("Please answer all questions before submitting."); field.focus(); return; }
        answers[field.dataset.question]=value;
    }
    try{
        const result=await api(`/questionnaires/${encodeURIComponent(teamId)}/answers`,{
            method:"PUT", body:JSON.stringify({answers})
        });
        state.answers[teamId]=result.answers;
        state.completed[teamId]=true;
        render();
    }catch(e){ alert(e.message); }
}

document.getElementById("resetBtn").addEventListener("click",logout);
window.addEventListener("load",boot);
