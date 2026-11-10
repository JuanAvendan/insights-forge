// Sample shop database for local testing, built from data/sample/sales.csv as
// *nested* documents, the way document data usually looks.
const fs = require("fs");

const shop = db.getSiblingDB("shop");
const lines = fs.readFileSync("/seed/sales.csv", "utf8").split(/\r?\n/).filter((l) => l.length);
const header = lines.shift().split(",");

const docs = lines.map((line) => {
  const values = line.split(",");
  const r = Object.fromEntries(header.map((h, i) => [h, values[i] === "" ? null : values[i]]));
  const doc = {
    order_id: r.order_id,
    order_date: new Date(`${r.order_date}T00:00:00Z`),
    region: r.region,
    channel: r.channel,
    product: { name: r.product, category: r.category },
    pricing: {
      unit_price: Number(r.unit_price),
      quantity: Number(r.quantity),
      discount: Number(r.discount),
    },
    total: Number(r.total),
    customer: r.customer_email === null ? {} : { email: r.customer_email },
    tags: [r.channel.toLowerCase(), r.category.toLowerCase()],
  };
  if (r.region === null) delete doc.region; // a missing field, as in real document data
  return doc;
});

shop.sales.insertMany(docs);

// Least privilege: the account used by insights-forge can only read.
shop.createUser({ user: "analyst", pwd: "analyst", roles: [{ role: "read", db: "shop" }] });
