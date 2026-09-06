import { workflow, node, trigger, expr, ifElse } from '@n8n/workflow-sdk';

const scheduleNode = trigger({
  type: 'n8n-nodes-base.scheduleTrigger',
  version: 1.1,
  config: {
    name: 'Schedule Trigger',
    parameters: {
      rule: {
        interval: [{ field: 'seconds', secondsInterval: 60 }]
      }
    }
  }
});

const sweepNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Postgres',
    parameters: {
      operation: 'executeQuery',
      query: 'SELECT sweep_expired_holds();'
    }
  }
});

const ifNode = ifElse({
  version: 2.2,
  config: {
    name: 'IF',
    parameters: {
      conditions: {
        options: { caseSensitive: true, typeValidation: 'loose' },
        conditions: [{ leftValue: expr('{{ $json.sweep_expired_holds }}'), operator: { type: 'number', operation: 'larger' }, rightValue: 0 }],
        combinator: 'and'
      }
    }
  }
});

const logNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Log to notification_logs',
    parameters: {
      operation: 'executeQuery',
      query: "INSERT INTO notification_logs (type, recipient, status, details) VALUES ('SYSTEM', 'ops-team', 'SENT', '{\"message\": \"Hold sweep completed\"}')"
    }
  }
});

export default workflow('01_hold_sweeper', '01_TTL_Seat_Hold_Sweeper')
  .add(scheduleNode)
  .to(sweepNode)
  .to(ifNode.onTrue(logNode));
