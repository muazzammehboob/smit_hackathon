import { workflow, node, trigger } from '@n8n/workflow-sdk';

const scheduleNode = trigger({
  type: 'n8n-nodes-base.scheduleTrigger',
  version: 1.1,
  config: {
    name: 'Schedule Trigger',
    parameters: {
      rule: {
        interval: [{ field: 'minutes', minutesInterval: 15 }]
      }
    }
  }
});

const scanBotsNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Scan Bots',
    parameters: {
      operation: 'executeQuery',
      query: "SELECT ip_address, COUNT(*) as cnt FROM bookings WHERE created_at > NOW() - INTERVAL '10 minutes' AND status != 'SUSPECTED_FRAUD' GROUP BY ip_address HAVING COUNT(*) >= 5;"
    }
  }
});

const updateBookingsNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Update Bookings',
    parameters: {
      operation: 'executeQuery',
      query: "UPDATE bookings SET status = 'SUSPECTED_FRAUD' WHERE ip_address = '{{$json.ip_address}}' AND created_at > NOW() - INTERVAL '10 minutes';"
    }
  }
});

const escalateNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Escalate',
    parameters: {
      operation: 'executeQuery',
      query: "INSERT INTO ops_escalations (reason, severity, details) VALUES ('BOT_BURST_DETECTED', 'HIGH', '{\"ip\": \"{{$json.ip_address}}\"}');"
    }
  }
});

export default workflow('03_fraud_scanner', '03_Fraud_Scanner')
  .add(scheduleNode)
  .to(scanBotsNode)
  .to(updateBookingsNode)
  .to(escalateNode);
