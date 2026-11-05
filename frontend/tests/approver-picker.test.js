import test from 'node:test';
import assert from 'node:assert/strict';
import { templateCandidateIds, pickerSections, newScopeCandidates } from '../src/approver-picker.js';
const a={user_id:'u1',name:'李伟俊'}, b={user_id:'u2',name:'张经理'}, c={user_id:'u3',name:'张总监'};
const local={selection_range_source:'local',allowed_user_ids:['u1'],template_allowed_user_ids:[]};
const sections=(overrides={})=>pickerSections({node:local,configuredUsers:[a],recent:[],results:[a,b,c],query:'',...overrides});
test('configured nodes can find directory people outside the editable local candidates',()=>{
 assert.deepEqual(sections().visibleConfigured,[a]); assert.deepEqual(sections().visibleResults,[b,c]);
});
test('name and case-insensitive UserId search applies to all sections',()=>{
 assert.deepEqual(sections({query:' 张 '}).visibleResults,[b,c]);
 assert.deepEqual(sections({query:' U2 '}).visibleResults,[b]);
 assert.deepEqual(sections({query:'伟俊'}).visibleConfigured,[a]);
 assert.equal(sections({query:'伟俊'}).matchingResults,1);
});
test('recent people remain available on configured nodes and match search',()=>{
 assert.deepEqual(sections({recent:[a,b],query:'张'}).visibleRecent,[b]);
 assert.deepEqual(sections({recent:[a,b],query:'张'}).visibleResults,[c]);
 assert.deepEqual(sections({recent:[b],query:'missing'}).visibleRecent,[]);
});
test('template range takes priority over configured people, recents and search',()=>{
 const result=sections({node:{...local,template_allowed_user_ids:['u2'],selection_range_source:'intersection'},recent:[a,b,c]});
 assert.deepEqual(result.visibleConfigured,[]); assert.deepEqual(result.visibleRecent,[b]);
 assert.deepEqual(result.visibleResults,[]); assert.equal(result.matchingResults,1);
});
test('older forecast IDs are template constraints unless their source is local',()=>{
 assert.deepEqual(templateCandidateIds({allowed_user_ids:['u1']}),['u1']);
 assert.deepEqual(templateCandidateIds({allowed_user_ids:['u1'],selection_range_source:'local'}),[]);
 assert.deepEqual(templateCandidateIds(local),[]);
});
test('unsupported template scopes never expose selectable people',()=>{
 const result=sections({node:{selection_range_kind:'unsupported'},recent:[a]});
 assert.deepEqual(result.visibleConfigured,[]); assert.deepEqual(result.visibleRecent,[]); assert.deepEqual(result.visibleResults,[]);
});
test('search results are unique and deduplicated against configured and recent sections',()=>{
 assert.deepEqual(sections({recent:[b,b],results:[a,a,b,b,c,c]}).visibleResults,[c]);
});
test('no configured scope still supports directory search',()=>{
 assert.deepEqual(sections({configuredUsers:[],query:'u2'}).visibleResults,[b]);
});
test('only explicitly chosen new people are appended, without replacing existing candidates',()=>{
 assert.deepEqual(newScopeCandidates([a,b,b], [a,c]),[b]);
 assert.deepEqual(newScopeCandidates([a], [a,c]),[]);
 assert.deepEqual(newScopeCandidates([], [a]),[]);
});
