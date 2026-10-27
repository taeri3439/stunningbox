const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function displayBox('), source.indexOf('function setGeometryBox(')), context);
const plain = value => Array.from(value);

test('viewer rectangles agree with PDF quarter-turn coordinates', () => {
  const box = [10,20,50,70];
  assert.deepEqual(plain(context.displayBox(box,{rotation:90,width:595,height:420})),[525,10,575,50]);
  assert.deepEqual(plain(context.displayBox(box,{rotation:180,width:420,height:595})),[370,525,410,575]);
  assert.deepEqual(plain(context.displayBox(box,{rotation:270,width:595,height:420})),[20,370,70,410]);
});
test('viewer drags and numeric edits round-trip across page sizes and rotations', () => {
  for(const [width,height] of [[420,595],[700,440],[612,792]]) {
    for(const rotation of [0,90,180,270]) {
      const p = rotation%180 ? {width:height,height:width,rotation} : {width,height,rotation};
      for(const box of [[0,0,width,height],[20,30,120,150],[width-50,height-60,width,height]]) {
        assert.deepEqual(plain(context.sourceBox(context.displayBox(box,p),p)),box);
      }
    }
  }
});
