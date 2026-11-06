const $=id=>document.getElementById(id);const fmt=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');const day=new Date(),after=new Date(day);after.setDate(after.getDate()+1);const params=new URLSearchParams(location.search);$('checkin').min=fmt(day);$('checkin').value=params.get('checkin')||fmt(day);$('checkout').min=fmt(after);$('checkout').value=params.get('checkout')||fmt(after);if(['single','double','deluxe'].includes(params.get('category')))$('category').value=params.get('category');const prices={single:1500,double:2000,deluxe:2500},capacity={single:1,double:2,deluxe:4};let current=null;const number=new Intl.NumberFormat('en-KE');function days(){return Math.round((new Date($('checkout').value+'T12:00:00')-new Date($('checkin').value+'T12:00:00'))/86400000)}function totals(){const n=days();$('summary').textContent=$('category').selectedOptions[0].text+' · '+(n>0?n:0)+' night(s)';$('total').textContent='KES '+number.format(prices[$('category').value]*Math.max(n,0))}$('payment_method').onchange=()=>{if($('payment_method').value==='mpesa')openPay()};$('category').onchange=()=>{$('guests').max=capacity[$('category').value];if(Number($('guests').value)>capacity[$('category').value])$('guests').value=capacity[$('category').value];totals()};$('checkin').onchange=()=>{const d=new Date($('checkin').value+'T12:00:00');d.setDate(d.getDate()+1);$('checkout').min=fmt(d);if($('checkout').value<$('checkout').min)$('checkout').value=$('checkout').min;totals()};$('checkout').onchange=totals;totals();const post=async(url,payload)=>{const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Request failed. Please try again.');return data};$('available').onsubmit=async e=>{e.preventDefault();try{const r=await fetch('/api/availability?'+new URLSearchParams({checkin:$('checkin').value,checkout:$('checkout').value}));const d=await r.json();if(!r.ok)throw Error(d.detail);const requested=$('category').value;const chosen=d.categories.find(x=>x.category===requested);$('availabilityStatus').textContent=(chosen?(chosen.available>0?'✓ '+chosen.available+' '+chosen.label+'(s) available for your dates.':'No '+chosen.label+' available for those dates.')+' ':'')+d.categories.map(x=>x.label+': '+x.available+' available').join(' · ');$('availabilityStatus').scrollIntoView({behavior:'smooth',block:'center'})}catch(e){$('availabilityStatus').textContent=e.message}};$('book').onsubmit=async e=>{e.preventDefault();$('bookingStatus').textContent='Saving reservation…';try{current=await post('/api/bookings',{category:$('category').value,guest_name:$('guest_name').value,email:$('email').value,phone:$('phone').value,checkin:$('checkin').value,checkout:$('checkout').value,guests:Number($('guests').value),requests:$('requests').value,payment_method:$('payment_method').value});$('bookingStatus').textContent='Booking successful! Your reference and private cancellation code are shown in the confirmation below. Please print or save them.';const rec=$('receipt');rec.hidden=false;$('receiptText').replaceChildren();const lines=[['Reference',current.booking_id],['Private code (keep safe)',current.cancel_code],['Guest',$('guest_name').value],['Room',$('category').selectedOptions[0].text],['Dates',$('checkin').value+' to '+$('checkout').value],['Amount','KES '+number.format(current.total)],['Status','Reservation received — pending staff approval'],['Payment','Unverified']];for(const [key,value] of lines){const p=document.createElement('p');p.textContent=key+': '+value;$('receiptText').append(p)}$('guestStkPhone').value=$('phone').value;$('cancelId').value=current.booking_id;$('cancelCode').value=current.cancel_code;$('payAgain').hidden=current.payment_method!=='mpesa';rec.scrollIntoView({behavior:'smooth'});if(current.payment_method==='mpesa')openPay();else rec.scrollIntoView({behavior:'smooth',block:'start'});loadGuestStk();refreshBookingState()}catch(err){$('bookingStatus').textContent=err.message}};function openPay(){if(current&&current.payment_method!=='mpesa')return;$('payTotal').textContent='KES '+number.format(current?current.total:prices[$('category').value]*Math.max(days(),1));$('codeStatus').textContent=current?'':'Complete your booking first to submit a payment transaction code.';$('paymentReference').textContent=current?'Booking reference: '+current.booking_id+' | Private code: '+current.cancel_code:'Complete your reservation first to get a reference and private code.';$('submitCode').hidden=!current;$('modal').hidden=false}$('payAgain').onclick=openPay;$('close').onclick=()=>$('modal').hidden=true;$('copy').onclick=()=>navigator.clipboard?.writeText('3340030').then(()=>$('copy').textContent='Copied!').catch(()=>$('copy').textContent='Copy manually: 3340030');$('submitCode').onsubmit=async e=>{e.preventDefault();if(!current)return;try{const result=await post('/api/bookings/mpesa/submit',{booking_id:current.booking_id,cancel_code:current.cancel_code,transaction_code:$('transaction_code').value.trim()});$('codeStatus').textContent=result.message;$('payAgain').hidden=true}catch(err){$('codeStatus').textContent=err.message}};$('cancel').onsubmit=async e=>{e.preventDefault();try{const x=await post('/api/bookings/cancel',{booking_id:$('cancelId').value.trim(),cancel_code:$('cancelCode').value.trim()});$('cancelStatus').textContent='Booking '+x.status;refreshBookingState()}catch(err){$('cancelStatus').textContent=err.message}};
if(params.has('checkin')&&params.has('checkout')){$('available').requestSubmit()}


async function latestBookingState(){
  if(!current)return null;
  const response=await fetch('/api/bookings/status',{method:'POST',headers:{'Content-Type':'application/json','Cache-Control':'no-store'},cache:'no-store',body:JSON.stringify({booking_id:current.booking_id,cancel_code:current.cancel_code})});
  if(!response.ok)throw Error('Unable to verify the latest reservation status. Printing is unavailable.');
  return await response.json();
}
async function refreshBookingState(){
  if(!current)return;
  try{
    const state=await latestBookingState();
    const blocked=!state.print_allowed;
    $('printAcknowledgment').disabled=blocked;
    $('payAgain').hidden=blocked||current.payment_method!=='mpesa';
    if(blocked){
      $('modal').hidden=true;
      $('printStatus').textContent='Acknowledgment printing disabled: reservation or payment rejected/cancelled.';
      $('receiptText').textContent='Reservation '+current.booking_id+' is '+state.status+'; payment status '+state.payment_status+'. Contact reception for assistance.';
    }else{
      $('printStatus').textContent='Reservation: '+state.status+' · Payment: '+state.payment_status+'. Printing checks the latest status.';
    }
  }catch(err){$('printAcknowledgment').disabled=true;$('printStatus').textContent=err.message}
}
$('printAcknowledgment').onclick=async()=>{
  if(!current)return;
  $('printAcknowledgment').disabled=true;
  $('printStatus').textContent='Checking the latest booking status before printing…';
  try{
    const state=await latestBookingState();
    if(!state.print_allowed){await refreshBookingState();return}
    const response=await fetch('/api/bookings/acknowledgment',{method:'POST',headers:{'Content-Type':'application/json','Cache-Control':'no-store'},cache:'no-store',body:JSON.stringify({booking_id:current.booking_id,cancel_code:current.cancel_code})});
    if(!response.ok){$('printStatus').textContent='Printing blocked: reservation is no longer eligible.';await refreshBookingState();return}
    const html=await response.text();
    const win=window.open('','_blank');
    if(!win){$('printStatus').textContent='Allow pop-ups for this site to print the latest acknowledgment.';return}
    win.document.open();win.document.write(html);win.document.close();win.focus();win.print();
    $('printStatus').textContent='Acknowledgment generated from the latest server status.';
  }catch(err){$('printStatus').textContent='Unable to print: '+err.message}
  finally{await refreshBookingState()}
};
window.addEventListener('focus',refreshBookingState);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshBookingState()});
setInterval(()=>{if(current&&!document.hidden)refreshBookingState()},15000);


let guestStkAvailable=false;
async function loadGuestStk(){
 try{
  const r=await fetch('/api/mpesa/stk/config',{cache:'no-store'});
  if(!r.ok)throw Error('Payment configuration unavailable.');
  const d=await r.json();
  guestStkAvailable=d.enabled===true;
  $('guestStkConfig').textContent=guestStkAvailable?'Request an M-Pesa payment prompt on your phone. You will confirm it using your own M-Pesa PIN.':'STK Push is being configured. Pay manually to Till '+d.till_number+' for now.';
  $('guestStkSend').disabled=!guestStkAvailable||!current;
 }catch(err){guestStkAvailable=false;$('guestStkSend').disabled=true;$('guestStkConfig').textContent='STK currently unavailable. Use manual Buy Goods payment.'}
}
$('guestStkSend').onclick=async()=>{
 if(!current||!guestStkAvailable)return;
 $('guestStkSend').disabled=true;
 $('guestStkMessage').textContent='Sending payment prompt to your M-Pesa phone…';
 try{
  const d=await post('/api/bookings/stk/request',{booking_id:current.booking_id,cancel_code:current.cancel_code,phone:$('guestStkPhone').value});
  $('guestStkMessage').textContent=d.message;
 }catch(error){$('guestStkMessage').textContent='STK request failed: '+error.message}
};
async function pollGuestStk(){
 if(!current||!guestStkAvailable||document.hidden)return;
 try{
  const response=await post('/api/bookings/stk/status',{booking_id:current.booking_id,cancel_code:current.cancel_code});
  if(response.stk_status==='verified'){
   $('guestStkMessage').textContent='Payment verified by Safaricom. Booking confirmed.';
   $('modal').hidden=true;await refreshBookingState();
  }else if(response.stk_status==='failed'){
   $('guestStkMessage').textContent='Payment was not completed. You can retry later or pay manually.';
  }else if(response.stk_status==='callback_success'){
   $('guestStkMessage').textContent='Payment response received; waiting for Safaricom verification.';
  }
 }catch(error){/* Do not infer payment success on network failure. */}
}
loadGuestStk();
setInterval(pollGuestStk,12000);
