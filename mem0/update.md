> ## Documentation Index
> Fetch the complete documentation index at: https://docs.mem0.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Update Memory

> Get or Update or delete a memory.



## OpenAPI

````yaml put /v1/memories/{memory_id}/
openapi: 3.0.1
info:
  title: Mem0 API Docs
  description: mem0.ai API Docs
  contact:
    email: deshraj@mem0.ai
  license:
    name: Apache 2.0
  version: v1
servers:
  - url: https://api.mem0.ai/
security:
  - ApiKeyAuth: []
paths:
  /v1/memories/{memory_id}/:
    put:
      tags:
        - memories
      description: Get or Update or delete a memory.
      operationId: memories_update
      parameters:
        - name: memory_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
          description: The unique identifier of the memory to retrieve.
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                text:
                  type: string
                  description: The updated text content of the memory
                metadata:
                  type: object
                  description: Additional metadata associated with the memory
      responses:
        '200':
          description: Successfully updated memory.
          content:
            application/json:
              schema:
                type: object
                properties:
                  id:
                    type: string
                    format: uuid
                    description: The unique identifier of the updated memory.
                  text:
                    type: string
                    description: The updated text content of the memory
                  user_id:
                    type: string
                    nullable: true
                    description: The user ID associated with the memory, if any
                  agent_id:
                    type: string
                    nullable: true
                    description: The agent ID associated with the memory, if any
                  app_id:
                    type: string
                    nullable: true
                    description: The app ID associated with the memory, if any
                  run_id:
                    type: string
                    nullable: true
                    description: The run ID associated with the memory, if any
                  hash:
                    type: string
                    description: Hash of the memory content
                  metadata:
                    type: object
                    description: Additional metadata associated with the memory
                  created_at:
                    type: string
                    format: date-time
                    description: Timestamp of when the memory was created.
                  updated_at:
                    type: string
                    format: date-time
                    description: Timestamp of when the memory was last updated.
      x-code-samples:
        - lang: Python
          source: >-
            # To use the Python SDK, install the package:

            # pip install mem0ai


            from mem0 import MemoryClient

            client = MemoryClient(api_key="your_api_key", org_id="your_org_id",
            project_id="your_project_id")


            # Update a memory

            memory_id = "<memory_id>"

            client.update(
                memory_id=memory_id,
                text="Your updated memory message here",
                metadata={"category": "example"}
            )
        - lang: JavaScript
          source: |-
            // To use the JavaScript SDK, install the package:
            // npm i mem0ai

            import MemoryClient from 'mem0ai';
            const client = new MemoryClient({ apiKey: "your-api-key" });

            // Update a specific memory
            const memory_id = "<memory_id>";
            client.update(memory_id, { 
              text: "Your updated memory message here",
              metadata: { category: "example" }
            })
              .then(result => console.log(result))
              .catch(error => console.error(error));
        - lang: cURL
          source: |-
            curl --request PUT \
              --url https://api.mem0.ai/v1/memories/{memory_id}/ \
              --header 'Authorization: Token <api-key>' \
              --header 'Content-Type: application/json' \
              --data '{"text": "Your updated memory text here", "metadata": {"category": "example"}}'
        - lang: Go
          source: "package main\n\nimport (\n\t\"fmt\"\n\t\"strings\"\n\t\"net/http\"\n\t\"io/ioutil\"\n)\n\nfunc main() {\n\n\turl := \"https://api.mem0.ai/v1/memories/{memory_id}/\"\n\n\tpayload := strings.NewReader(`{\n\t\"text\": \"Your updated memory text here\",\n\t\"metadata\": {\n\t\t\"category\": \"example\"\n\t}\n}`)\n\n\treq, _ := http.NewRequest(\"PUT\", url, payload)\n\n\treq.Header.Add(\"Authorization\", \"Token <api-key>\")\n\treq.Header.Add(\"Content-Type\", \"application/json\")\n\n\tres, _ := http.DefaultClient.Do(req)\n\n\tdefer res.Body.Close()\n\tbody, _ := ioutil.ReadAll(res.Body)\n\n\tfmt.Println(res)\n\tfmt.Println(string(body))\n\n}"
        - lang: PHP
          source: |-
            <?php

            $curl = curl_init();

            curl_setopt_array($curl, [
              CURLOPT_URL => "https://api.mem0.ai/v1/memories/{memory_id}/",
              CURLOPT_RETURNTRANSFER => true,
              CURLOPT_ENCODING => "",
              CURLOPT_MAXREDIRS => 10,
              CURLOPT_TIMEOUT => 30,
              CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
              CURLOPT_CUSTOMREQUEST => "PUT",
              CURLOPT_HTTPHEADER => [
                "Authorization: Token <api-key>",
                "Content-Type: application/json"
              ],
              CURLOPT_POSTFIELDS => json_encode([
                "text" => "Your updated memory text here",
                "metadata" => ["category" => "example"]
              ])
            ]);

            $response = curl_exec($curl);
            $err = curl_error($curl);

            curl_close($curl);

            if ($err) {
              echo "cURL Error #:" . $err;
            } else {
              echo $response;
            }
        - lang: Java
          source: >-
            HttpResponse<String> response =
            Unirest.put("https://api.mem0.ai/v1/memories/{memory_id}/")
              .header("Authorization", "Token <api-key>")
              .header("Content-Type", "application/json")
              .body("{\"text\": \"Your updated memory text here\", \"metadata\": {\"category\": \"example\"}}")
              .asString();
components:
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: Authorization
      description: >-
        API key authentication. Prefix your Mem0 API key with 'Token '. Example:
        'Token your_api_key'

````

Built with [Mintlify](https://mintlify.com).