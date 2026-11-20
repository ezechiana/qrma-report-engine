(() => {
 const el=id=>document.getElementById(id), providers=el('sim-providers');let busy=false;
 const labels={google:'Google',microsoft:'Microsoft'};
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 function error(message){el('sim-error').textContent=message;el('sim-error').hidden=false;el('sim-error').focus();}
 function controls(){document.querySelectorAll('button').forEach(b=>{b.disabled=busy||b.dataset.disabled==='true';});}
 async function api(path,options={}) {
  const r=await fetch(path,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json',...(options.headers||{})}});
  if(r.status===401){el('sim-login').hidden=false;throw new Error('Sign in again before managing sign-in methods.');}
  const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Could not complete this action. Reload and try again.');return data;
 }
 async function load(){
  busy=true;controls();el('sim-error').hidden=true;el('sim-status').textContent='Loading sign-in methods…';
  try{
   const data=await api('/auth/sign-in-methods');providers.replaceChildren();
   el('sim-account').textContent=`GO360 account: ${data.email}`;el('sim-return').href=data.return_url;
   el('sim-read-only').hidden=!data.read_only;el('sim-read-only').textContent=data.read_only_reason||'';
   for(const p of data.providers){
    const card=node('article','');card.dataset.provider=p.provider;card.append(node('h2',labels[p.provider]));
    if(p.linked){card.append(node('p',p.email?`Linked: ${p.email}`:'Linked to this GO360 account.'));}
    else if(p.pending){
     card.append(node('p',`Verified provider account: ${p.pending.email}`),node('p','Confirm that you want this provider account to sign in to the GO360 account shown above. Enter your current GO360 password.'));
     const form=document.createElement('form'),input=document.createElement('input'),label=node('label','Current GO360 password');
     input.id='sim-password-'+p.provider;input.type='password';input.autocomplete='current-password';input.required=true;input.maxLength=128;label.htmlFor=input.id;
     const button=node('button',`Confirm ${labels[p.provider]} link`);button.type='submit';button.dataset.disabled=String(data.read_only);
     form.append(label,input,button);card.append(form);
     form.addEventListener('submit',async e=>{
      e.preventDefault();if(busy||data.read_only)return;busy=true;controls();el('sim-error').hidden=true;
      const password=input.value;input.value='';
      try{await api('/auth/sign-in-methods/confirm/'+encodeURIComponent(p.pending.id),{method:'POST',body:JSON.stringify({current_password:password})});
       await load();el('sim-status').textContent='Sign-in method linked. Your existing account and permissions are unchanged.';
      }catch(e){error(e.message);}finally{busy=false;controls();}
     });
    }else{
     card.append(node('p',p.configured?'Not linked.':'This provider is not configured.'));
     const button=node('button',`Link ${labels[p.provider]}`);button.type='button';button.dataset.disabled=String(data.read_only||!p.configured);
     button.addEventListener('click',async()=>{
      if(busy||data.read_only)return;busy=true;controls();el('sim-error').hidden=true;
      try{const result=await api('/auth/sign-in-methods/'+p.provider+'/link',{method:'POST',body:'{}'});window.location.assign(result.url);}
      catch(e){error(e.message);busy=false;controls();}
     });card.append(button);
    }providers.append(card);
   }el('sim-status').textContent='';
  }catch(e){error(e.message);el('sim-status').textContent='';}
  finally{busy=false;controls();}
 }
 el('sim-reload').addEventListener('click',load);
 load().then(()=>{const params=new URLSearchParams(location.search);if(params.has('link_error'))error(params.get('link_error')==='cancelled'?'Linking was cancelled. No sign-in method changed.':'Linking could not be verified. Start again from this account.');history.replaceState(null,'',location.pathname);});
})();
