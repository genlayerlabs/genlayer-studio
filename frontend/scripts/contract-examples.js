import { existsSync } from 'node:fs';
import pkg from 'ncp';
const { ncp } = pkg;
import path from 'node:path';
import fs from 'node:fs';

ncp.limit = 16;
const source = path.resolve('../examples');
const destionation =  path.resolve('./src/assets/examples');
if (existsSync(destionation)) {
    console.log('Contract Examples already exists');
    try {
        fs.rmSync(destionation, { recursive: true, force: true });
    } catch (error) {
        console.log('Could not remove existing examples, copying anyway...');
    }
}

ncp(source, destionation,
        function (err) {
    if (err) {
        return console.error(err);
    }
    console.log('Contract Examples copied');
});
