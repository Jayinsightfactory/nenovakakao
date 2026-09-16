// Read-only snapshot via the ERP's existing DB configuration. Never log secrets.
const fs=require('fs');
const path=require('path');
const {createRequire}=require('module');
const erp=process.argv[2];
const out=process.argv[3];
const req=createRequire(path.join(erp,'package.json'));
const sql=req('mssql');
process.loadEnvFile(path.join(erp,'.env.local'));
async function main(){
 const pool=await new sql.ConnectionPool({server:process.env.DB_SERVER,
  port:Number(process.env.DB_PORT||1433), database:process.env.DB_NAME,
  user:process.env.DB_USER,password:process.env.DB_PASSWORD,
  options:{encrypt:false,trustServerCertificate:true,enableArithAbort:true},
  connectionTimeout:15000,requestTimeout:30000,pool:{max:1,min:0}}).connect();
 try{
  const query=`SELECT e.EstimateKey,e.EstimateType,e.ProdKey,e.Unit,e.Quantity,e.Descr,e.EstimateDtm,
   sm.ShipmentKey,sm.OrderYear,sm.OrderWeek,sm.CustKey,sm.isFix,
   p.ProdName,p.FlowerName,p.CounName,p.SteamOf1Bunch,p.BunchOf1Box,p.SteamOf1Box,
   c.CustName,c.Manager
   FROM Estimate e JOIN ShipmentMaster sm ON sm.ShipmentKey=e.ShipmentKey
   LEFT JOIN Product p ON p.ProdKey=e.ProdKey LEFT JOIN Customer c ON c.CustKey=sm.CustKey
   WHERE sm.OrderYear=@year AND sm.isDeleted=0 AND sm.isFix=1
   AND TRY_CONVERT(int,LEFT(sm.OrderWeek,2)) BETWEEN 30 AND 37
   AND (e.EstimateType IN ('FEE03-KR0009','FEE03-KR0010','FEE03-KR0011','FEE03-KR0020','FEE03-KR0024')
     OR e.EstimateType LIKE @type) ORDER BY sm.OrderWeek,c.CustName,e.EstimateKey`;
  const result=await pool.request().input('year',sql.NVarChar,'2026')
    .input('type',sql.NVarChar,'불량차감%').query(query);
  if(!result.recordset.length){
    const info=await pool.request().query(`SELECT TOP 30 sm.OrderYear,sm.OrderWeek,sm.isFix,sm.isDeleted,e.EstimateType,COUNT(*) AS n
      FROM Estimate e JOIN ShipmentMaster sm ON sm.ShipmentKey=e.ShipmentKey
      GROUP BY sm.OrderYear,sm.OrderWeek,sm.isFix,sm.isDeleted,e.EstimateType ORDER BY sm.OrderYear DESC,sm.OrderWeek DESC`);
    console.log(JSON.stringify(info.recordset));
  }
  fs.writeFileSync(out,JSON.stringify({read_at:new Date().toISOString(),read_only:true,query,
    rows:result.recordset},null,2));
  console.log('defect rows',result.recordset.length);
 } finally {await pool.close();}
}
main().catch(e=>{console.error('Read failed:',e.code||e.name);process.exitCode=1;});
