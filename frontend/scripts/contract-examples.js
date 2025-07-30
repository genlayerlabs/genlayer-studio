import { existsSync } from 'node:fs';
import pkg from 'ncp';
const { ncp } = pkg;
import path from 'node:path';
import fs from 'node:fs';

ncp.limit = 16;
const source = path.resolve('../examples');
const destination =  path.resolve('./src/assets/examples');
if (existsSync(destination)) {
    console.log('Contract Examples already exists');
    fs.rmSync(destination, { recursive: true, force: true });
}

ncp(source, destionation,
        function (err) {
    if (err) {
        return console.error(err);
    }
    console.log('Contract Examples copied');
});
