const https = require('https');
const url = 'https://web-production-1df42.up.railway.app/api/audits?type=competitor';

https.get(url, (res) => {
  let data = '';
  res.on('data', (chunk) => { data += chunk; });
  res.on('end', () => {
    console.log(data);
  });
}).on('error', (err) => {
  console.log("Error: " + err.message);
});
