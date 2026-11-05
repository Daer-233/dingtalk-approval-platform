import test from 'node:test';
import assert from 'node:assert/strict';
import {restoredWorkflowTab,forecastActioners} from '../src/workflow-tabs.js';
import {templateCandidateIds,pickerSections} from '../src/approver-picker.js';
test('unprepared tasks restore groups; attachments restore nodes',()=>{
 assert.equal(restoredWorkflowTab(null),'groups');
 assert.equal(restoredWorkflowTab({status:'review'}),'groups');
 assert.equal(restoredWorkflowTab({status:'prepared',result:{}}),'nodes');
});
test('preview and all submission histories restore preview without reselecting on polling',()=>{
 for(const status of ['previewed','submitting','submitted','partial_failed']) assert.equal(restoredWorkflowTab({status,result:{}}),'preview');
 assert.equal(restoredWorkflowTab({status:'failed',result:{},approval_attempts:[{}]}),'preview');
 assert.equal(restoredWorkflowTab({result:{},previews:[{}]}),'preview');
});
test('server seeded CC users and deliberate empty per-group selections are retained',()=>{
 const people=[{user_id:'template',source:'template'},{user_id:'local',source:'local_default'}];
 const actual=forecastActioners({g1:{node_actioners:{cc:people}},g2:{node_actioners:{cc:[]}}});
 assert.deepEqual(actual,{g1:{cc:people},g2:{cc:[]}});
 assert.deepEqual(forecastActioners({g1:{error:'prediction failed'}}),{g1:{}});
});
test('immutable template CC users stay visible even when local candidate intersection excludes them',()=>{
 const node={template_allowed_user_ids:['local'],template_default_user_ids:['template'],selection_range_kind:'users'};
 assert.deepEqual(templateCandidateIds(node),['local','template']);
 const people=[{user_id:'template',name:'模板默认'},{user_id:'outside',name:'越权'}];
 const sections=pickerSections({node,configuredUsers:[],recent:[],results:people,query:''});
 assert.deepEqual(sections.visibleResults,[people[0]]);
});
test('template CC defaults never turn an otherwise unrestricted search into a template-only restriction',()=>{
 assert.deepEqual(templateCandidateIds({template_allowed_user_ids:[],template_default_user_ids:['template'],selection_range_source:'local'}),[]);
});
