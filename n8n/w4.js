import { workflow, node, trigger } from '@n8n/workflow-sdk';

const webhookNode = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 1,
  config: {
    name: 'Webhook',
    parameters: {
      httpMethod: 'POST',
      path: 'rag-query'
    }
  }
});

const getBookingNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Get Booking',
    parameters: {
      operation: 'executeQuery',
      query: "SELECT fare_class FROM bookings WHERE pnr = '{{$json.body.pnr}}';"
    }
  }
});

const vectorSearchNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Vector Search',
    parameters: {
      operation: 'executeQuery',
      query: "SELECT content FROM match_policy_embeddings('[0]', '{{$json.fare_class}}', 3);"
    }
  }
});

const llmNode = node({
  type: 'n8n-nodes-base.openAi',
  version: 1,
  config: {
    name: 'OpenAI',
    parameters: {
      model: 'gpt-3.5-turbo',
      prompt: 'Draft customer response based on context'
    }
  }
});

const saveDraftNode = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.3,
  config: {
    name: 'Save Draft',
    parameters: {
      operation: 'executeQuery',
      query: "INSERT INTO support_draft_approvals (pnr, question, draft_response, status) VALUES ('{{$node[\"Webhook\"].json.body.pnr}}', '{{$node[\"Webhook\"].json.body.question}}', '{{$json.response}}', 'PENDING') RETURNING id;"
    }
  }
});

const notifyOpsNode = node({
  type: 'n8n-nodes-base.emailSend',
  version: 2,
  config: {
    name: 'Email Ops',
    parameters: {
      toEmail: 'ops@example.com',
      subject: 'Approval Required',
      text: 'Please approve: /api/v1/support/approve?id={{$json.id}}&action=approve'
    }
  }
});

export default workflow('04_rag_approval_gate', '04_RAG_Policy_Human_Approval_Gate')
  .add(webhookNode)
  .to(getBookingNode)
  .to(vectorSearchNode)
  .to(llmNode)
  .to(saveDraftNode)
  .to(notifyOpsNode);
