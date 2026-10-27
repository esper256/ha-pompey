/* Parse browser scripts without executing them or starting a browser. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../pompey/rootfs/usr/share/pompey');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
for (const [index, match] of [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].entries()) {
  new vm.Script(match[1], {filename: `index.html:inline-${index}`});
}
for (const file of fs.readdirSync(root).filter(name => name.endsWith('.js'))) {
  new vm.Script(fs.readFileSync(path.join(root, file), 'utf8'), {filename: file});
}
