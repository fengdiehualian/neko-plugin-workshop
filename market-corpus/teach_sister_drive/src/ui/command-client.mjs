// A lost response does not imply a lost action. Reconcile the same request ID;
// never automatically re-issue a vehicle action with a new ID.
export async function executeCommand({requestId, post, lookup, wait, isCurrent, attempts = 12}) {
  let receipt;
  try { receipt = await post(); }
  catch (problem) { receipt = problem.receipt; }
  for (let attempt = 0; attempt <= attempts; attempt++) {
    if (!isCurrent()) throw Object.assign(new Error('关卡或会话已切换'), {cancelled:true});
    if (receipt && ['executed','rejected','expired'].includes(receipt.status)) {
      if (receipt.request_id !== requestId) break;
      return receipt;
    }
    if (attempt === attempts) break;
    await wait(150);
    try { receipt = await lookup(requestId); }
    catch (problem) { receipt = problem.receipt; }
  }
  throw Object.assign(new Error('执行结果还未确认，请查看车辆状态后再下令'), {uncertain:true});
}
