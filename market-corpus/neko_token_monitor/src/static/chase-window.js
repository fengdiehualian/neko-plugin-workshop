(()=>{
 const stage=new window.CatfoodChaseStage(),bridge=window.webkit?.messageHandlers?.nekoWidget;
 window.catfoodChaseSetup=value=>stage.configure(value);
 window.catfoodChaseInspect=()=>{stage.inspectionPaused=true;};
 window.catfoodChaseNativeCat=()=>{stage.nativeCat=true;stage.cat.style.opacity='1';};
 window.catfoodChaseDrag=value=>{if(value.phase==='start'){if(stage.nativeCat)stage.cat.style.opacity='1';stage.beginDrag(value);}else if(value.phase==='move')stage.moveDrag(value);else if(value.phase==='end')stage.endDrag(value);else stage.cancelDrag();};
 window.addEventListener('catfood:gameevent',e=>bridge?.postMessage({action:'chaseEvent',...e.detail}));
 window.addEventListener('catfood:chasegeometry',e=>bridge?.postMessage({action:'chaseGeometry',rect:e.detail}));
 window.addEventListener('catfood:chasebite',e=>bridge?.postMessage({action:'chaseCaught',...e.detail}));
 window.addEventListener('catfood:catreturned',e=>bridge?.postMessage({action:'catReturned',...e.detail}));
 const ready=async()=>{try{await stage.ready;await Promise.all([...stage.layer.querySelectorAll('img')].map(img=>img.decode()));stage.start();bridge?.postMessage('chaseReady');}catch{bridge?.postMessage('chaseAssetError');}};
 ready();window.addEventListener('pagehide',()=>stage.destroy());
})();
