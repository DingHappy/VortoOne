import assert from "node:assert/strict";
import {createServer} from "vite";
import {fileURLToPath, pathToFileURL} from "node:url";
import React from "react";
// Optional QA tool, deliberately absent from product dependencies.
if (!process.argv[2]) throw new Error("Pass the absolute path to a matching react-test-renderer index.js");
const {create,act}=await import(pathToFileURL(process.argv[2]).href);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.document={hidden:false,activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return null;}};
globalThis.HTMLElement=class {};
const server=await createServer({root:fileURLToPath(new URL("../", import.meta.url)),configFile:false,esbuild:{jsx:"automatic"},server:{middlewareMode:true,hmr:false,ws:false},appType:"custom"});
const {RoleConversation}=await server.ssrLoadModule("/src/features/roles/RoleConversation.tsx");
const {RoleCreateDialog}=await server.ssrLoadModule("/src/features/roles/RoleCreateDialog.tsx");
const {roleDrafts}=await server.ssrLoadModule("/src/features/roles/conversationController.ts");
const role={id:"reader",title:"Reader",brief:"Registered",revision:1,enabled:true,builtin:false,engine:["vortocode"],grants:{operations:["read"],tools:[],sees:[],web:false,project:false}};
const member={id:"reader",name:"Reader",role:"reader",revision:1,status:"active",recent_tasks:[],model:"",busy:0};
const registry={roles:[role],members:[member],proposals:[],conversation_version:1,owner_instance:"instance",role_digests:{reader:"digest"}};
let reads=0,proposals=0;
const client={readRoleConversation:async()=>{reads++;return{version:1,instance:"instance",identity_digest:"identity",role_digest:"digest",member,role,tasks:[],can_dispatch:true,unavailable_reason:"",history_complete:true,next_cursor:null,submitted_task:null};},
readStaff:async()=>structuredClone(registry),proposeStaffMember:async(input)=>{proposals++;return{id:"proposal",status:"pending",digest:"digest",nonce:"nonce",round:1,instance:"instance",snapshot:{kind:"member",data:{id:"staff-"+input.request_id,name:input.name,role:"reader"}}};}};
const lease={client,current:()=>true,target:{targetKey:"space",runtimeLabel:"Mac"}};
const timers=new Map();let timerId=0;
const oldTimeout=globalThis.setTimeout,oldClear=globalThis.clearTimeout;
globalThis.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};
globalThis.clearTimeout=id=>timers.delete(id);
let conversation,creation;const result={};
try {
await act(async()=>{conversation=create(React.createElement(React.StrictMode,null,React.createElement(RoleConversation,{memberId:"reader",name:"Reader",lease,drafts:roleDrafts(),onProfile(){},onMain(){}})));});
await act(async()=>{for(const [id,t] of [...timers])if(t.ms===0){timers.delete(id);await t.fn();}});
result.conversationReads=reads;
result.conversationReady=JSON.stringify(conversation.toJSON()).includes("默认由你验收");
await act(async()=>{conversation.unmount();});
await act(async()=>{creation=create(React.createElement(React.StrictMode,null,React.createElement(RoleCreateDialog,{registry,lease,onRegistered(){},onClose(){}})));});
await act(async()=>{creation.root.findAllByType("input").find(n=>n.props.maxLength===80).props.onChange({target:{value:"Reader Two"}});});
await act(async()=>{creation.root.findByType("form").props.onSubmit({preventDefault(){}});});
await act(async()=>{creation.root.findByType("form").props.onSubmit({preventDefault(){}});});
result.proposals=proposals;
result.proposalReady=JSON.stringify(creation.toJSON()).includes("批准并添加角色");
await act(async()=>{creation.unmount();});
console.log(JSON.stringify(result));
assert.equal(reads,1,"StrictMode must leave one live conversation reader");
assert.equal(proposals,1,"StrictMode must leave one live role creation controller");
assert.equal(result.conversationReady,true,"The live reader must publish the loaded conversation");
assert.equal(result.proposalReady,true,"The live creation controller must publish the pending proposal");
} finally {globalThis.setTimeout=oldTimeout;globalThis.clearTimeout=oldClear;await server.close();}
