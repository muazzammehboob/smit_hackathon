import { workflow, node, trigger, expr, ifElse } from '@n8n/workflow-sdk';

const scheduleNode = trigger({
  type: 'n8n-nodes-base.scheduleTrigger',
  version: 1.1,
  config: {
    name: 'Schedule Trigger',
    parameters: {
      rule: {
        interval: [{ field: 'minutes', minutesInterval: 2 }]
      }
    }
  }
});

const waitlistPromoteNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Promote Waitlist',
    parameters: {
      operation: 'executeQuery',
      query: "SELECT promote_waitlist(f.id) FROM flights f JOIN flight_classes fc ON f.id = fc.flight_id WHERE (fc.capacity - fc.booked_seats) > 0 AND EXISTS (SELECT 1 FROM waitlist WHERE flight_id = f.id AND status = 'WAITING') LIMIT 5;"
    }
  }
});

const ifNode = ifElse({
  version: 2.2,
  config: {
    name: 'Passenger Returned?',
    parameters: {
      conditions: {
        options: { caseSensitive: true, typeValidation: 'loose' },
        conditions: [{ leftValue: expr('{{ $json.promote_waitlist }}'), operator: { type: 'string', operation: 'notEmpty' } }],
        combinator: 'and'
      }
    }
  }
});

const notifyNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Log Notification',
    parameters: {
      operation: 'executeQuery',
      query: "INSERT INTO notification_logs (type, recipient, status, details) VALUES ('SYSTEM', 'ops-team', 'SENT', '{\"message\": \"Waitlist promotion completed\"}')"
    }
  }
});

export default workflow('02_waitlist_promoter', '02_Waitlist_Auto_Promoter')
  .add(scheduleNode)
  .to(waitlistPromoteNode)
  .to(ifNode.onTrue(notifyNode));
