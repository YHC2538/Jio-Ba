/* global use, db */
// MongoDB Playground
// To disable this template go to Settings | MongoDB | Use Default Template For Playground.
// Make sure you are connected to enable completions and to be able to run a playground.
// Use Ctrl+Space inside a snippet or a string literal to trigger completions.
// The result of the last command run in a playground is shown on the results panel.
// By default the first 20 documents will be returned with a cursor.
// Use 'console.log()' to print to the debug output.
// For more documentation on playgrounds please refer to
// https://www.mongodb.com/docs/mongodb-vscode/playgrounds/
use('jio_ba_bot');
db.getCollection('events').aggregate([
  { $match: { "participants.user_name": "KTK" } },
  { $unwind: "$participants" },
  { $match: { "participants.user_name": "KTK" } },
  { $unwind: "$participants.conversation_history" },
  { $sort: { "participants.conversation_history.timestamp": 1 } },
  { $project: {
      _id: 0,
      event_title: "$title",
      time: "$participants.conversation_history.timestamp",
      author: "$participants.conversation_history.author_name",
      role: "$participants.conversation_history.role",
      message: { $arrayElemAt: ["$participants.conversation_history.parts", 0] }
  }}
]);
