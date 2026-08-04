from openai import OpenAI

client = OpenAI()

try:
    response = client.responses.create(
        model="gpt-5-mini",
        input="请只回复：API 测试成功"
    )

    print(response.output_text)

except Exception as error:
    print("API 测试失败：")
    print(error)