const path=require('node:path');

// Serve checkout assets in the isolated browser context. A different N.E.K.O
// installation may own localhost:48911; tests must not depend on or modify it.
async function serveLocalStaticAssets(context) {
  const root=path.resolve(__dirname,'../../../../static');
  await context.route('http://localhost:48911/static/**',async route=>{
    const relative=decodeURIComponent(new URL(route.request().url()).pathname.slice('/static/'.length));
    const uiPrefix='game/teach-sister-drive/';
    const uiRoot=path.resolve(__dirname,'../ui');
    const file=relative.startsWith(uiPrefix) ? path.resolve(uiRoot,relative.slice(uiPrefix.length)) : path.resolve(root,relative);
    if(relative.startsWith(uiPrefix) && !file.startsWith(uiRoot+path.sep))return route.fulfill({status:404,body:''});
    if(!relative.startsWith(uiPrefix) && !file.startsWith(root+path.sep))return route.fulfill({status:404,body:''});
    const contentType={'.mjs':'text/javascript','.js':'text/javascript','.html':'text/html',
      '.css':'text/css','.json':'application/json'}[path.extname(file)];
    try {await route.fulfill({path:file,...(contentType?{contentType}:{})});}
    catch {await route.fulfill({status:404,body:''});}
  });
}
module.exports={serveLocalStaticAssets};
