import test from 'node:test';
import assert from 'node:assert/strict';
import {nodeCompatibilityView} from '../src/workflow-compatibility.js';
const path={code:'path_unconfirmed',activity_id:'',message:'opaque route'};
test('legacy global path error does not accuse CC users',()=>{
 const value=nodeCompatibilityView({activity_id:'cc',compatibility:{status:'incompatible',issues:[path]}},{status:'incompatible'});
 assert.equal(value.label,'节点校验通过');assert.deepEqual(value.issues,[]);assert.notEqual(value.label,'可提交');
});
test('audit problem is only on audit; other nodes remain verified, not submittable',()=>{
 const audit={code:'unsupported_role',activity_id:'audit',message:'办理人不可转换'};
 assert.equal(nodeCompatibilityView({activity_id:'audit',compatibility:{status:'incompatible',issues:[path,audit]}},{status:'incompatible'}).label,'不兼容');
 assert.equal(nodeCompatibilityView({activity_id:'cc',compatibility:{status:'verified',issues:[]}},{status:'incompatible'}).label,'节点校验通过');
});
test('a genuine CC range issue stays incompatible',()=>{
 assert.equal(nodeCompatibilityView({activity_id:'cc',compatibility:{status:'incompatible',issues:[{activity_id:'cc',code:'selection_range'}]}}).status,'incompatible');
});
test('only a wholly eligible workflow shows submittable',()=>{
 assert.equal(nodeCompatibilityView({activity_id:'cc',compatibility:{status:'submittable',issues:[]}},{status:'submittable'}).label,'可提交');
});
test('unexplained failures remain blocked and absent checks are not fabricated',()=>{
 assert.equal(nodeCompatibilityView({activity_id:'cc',compatibility:{status:'incompatible',issues:[]}}).label,'不兼容');
 assert.equal(nodeCompatibilityView({activity_id:'cc'}),null);
});
test('view never modifies stored errors, eligibility, or confirmation',()=>{
 const value={activity_id:'cc',compatibility:{status:'incompatible',issues:[path]}};const old=JSON.stringify(value);
 nodeCompatibilityView(value,{status:'incompatible'});assert.equal(JSON.stringify(value),old);
});

test('recognized template-owned handler is delegated, not an incompatibility or completed state',()=>{
 const n={activity_id:'cashier',actor_type:'audit',kind:'fixed',compatibility:{status:'verified',execution_owner:'dingtalk',preserves_template:true,issues:[],workflow_blocked:true}};
 const v=nodeCompatibilityView(n,{status:'incompatible'});
 assert.equal(v.status,'delegated');assert.equal(v.label,'钉钉后续办理');assert.match(v.hint,/整条流程/);assert.doesNotMatch(v.label,/已完成|已办理|可提交/);
});
test('handler with a genuine own error remains incompatible despite template ownership',()=>{
 const n={activity_id:'cashier',actor_type:'audit',kind:'fixed',compatibility:{status:'incompatible',execution_owner:'dingtalk',preserves_template:true,issues:[{activity_id:'cashier',code:'actioners_unconfirmed'}]}};
 assert.equal(nodeCompatibilityView(n).status,'incompatible');
});
test('legacy unsupported handler badges are not silently reinterpreted as template-owned',()=>{
 const n={activity_id:'cashier',actor_type:'audit',kind:'fixed',compatibility:{status:'incompatible',issues:[{activity_id:'cashier',code:'unsupported_role'}]}};
 assert.equal(nodeCompatibilityView(n).status,'incompatible');
});
test('manual handler still needs selection and verified binding, not a fixed delegation badge',()=>{
 const n={activity_id:'cashier',actor_type:'audit',kind:'manual',compatibility:{status:'verified',execution_owner:'dingtalk',preserves_template:true,issues:[],workflow_blocked:true}};
 assert.notEqual(nodeCompatibilityView(n).status,'delegated');
});
