const test=require('node:test');
const assert=require('node:assert/strict');
const {helperSource}=require('./patch-payment-platform.cjs');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(helperSource).toString('base64'));
test('explicit task IDs, absent IDs and malformed links remain distinct',async()=>{
 const {linkedTaskId}=await modulePromise;
 const id='abcdeabcdeabcdeabcdeabcdeabcdeab';
 const uuid='11111111-2222-3333-4444-555555555555';
 assert.equal(linkedTaskId(''),undefined);
 assert.equal(linkedTaskId('?tab=workspace'),undefined);
 assert.equal(linkedTaskId('?task_id='+uuid),uuid);
 assert.equal(linkedTaskId('?task_id='+'a'.repeat(32)),'a'.repeat(32));
 for(const value of ['', '../other','https://evil.example','<script>',' a'.repeat(32)])assert.equal(linkedTaskId('?task_id='+encodeURIComponent(value)),null);
});
